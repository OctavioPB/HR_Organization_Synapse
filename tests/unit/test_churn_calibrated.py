"""Unit tests: calibrated churn model and peer exposure (ml/churn)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from ml.churn.contagion import departure_weights, peer_exposure, select_peer_exposure, tie_share_matrix
from ml.churn.model import (
    at_risk_labels,
    calibration_error,
    fit_churn_model,
    risk_tier,
    select_champion,
    temporal_folds,
)
from ml.churn.pipeline import anchor_dates
from ml.stats import benjamini_hochberg, permutation_pvalue

T = date(2026, 3, 1)


class TestLabels:
    def test_at_risk_excludes_future_hires_and_past_leavers(self):
        emps = [
            {"id": "new", "hire_date": T + timedelta(days=1)},
            {"id": "gone", "hire_date": date(2020, 1, 1)},
            {"id": "stay", "hire_date": date(2020, 1, 1)},
            {"id": "leaver", "hire_date": None},
        ]
        deps = {"gone": T, "leaver": T + timedelta(days=60)}
        assert at_risk_labels(emps, deps, T, horizon_days=90) == [("stay", 0), ("leaver", 1)]

    def test_departure_after_horizon_is_negative(self):
        deps = {"a": T + timedelta(days=91)}
        assert at_risk_labels([{"id": "a"}], deps, T, 90) == [("a", 0)]


class TestTemporalFolds:
    def test_folds_are_ordered_and_purged(self):
        anchors = np.repeat([date(2025, 1, 1).toordinal() + 14 * k for k in range(20)], 5)
        train, test = temporal_folds(anchors, horizon_days=90)
        assert train.any() and test.any() and not (train & test).any()
        assert anchors[train].max() < anchors[test].min()
        # No training label window reaches the test fold.
        assert (anchors[train] + 90 < anchors[test].min()).all()

    def test_too_few_anchors_gives_empty_folds(self):
        train, test = temporal_folds(np.array([1, 1, 1]), 90)
        assert not (train.any() or test.any())


class TestModel:
    def test_refuses_to_train_on_too_few_departures(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(400, 3))
        y = np.zeros(400, dtype=int)
        y[:5] = 1
        anchors = np.repeat(np.arange(20) * 200, 20)
        assert fit_churn_model(X, y, anchors, horizon_days=90, min_positives=20) is None

    def test_calibration_error_zero_for_perfect_forecast(self):
        p = np.array([0.1] * 50 + [0.5] * 50)
        y = np.array([1] * 5 + [0] * 45 + [1] * 25 + [0] * 25)
        assert calibration_error(y, p, n_bins=2) == pytest.approx(0.0)

    def test_drivers_point_at_positive_contributions(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(4000, 2))
        y = (rng.random(4000) < 1 / (1 + np.exp(-(2 * X[:, 0] - 3)))).astype(int)
        anchors = np.repeat(np.arange(40) * 200, 100)
        model = fit_churn_model(X, y, anchors, horizon_days=90)
        model.feature_names = ["risky", "noise"]
        assert model.drivers(np.array([2.0, 0.0]), k=1)[0]["feature"] == "risky"


class TestChampion:
    base = {"auroc": 0.72, "brier": 0.060}

    def test_baseline_kept_when_graph_model_loses(self):
        assert select_champion(self.base, {"auroc": 0.62, "brier": 0.07}) == "baseline"

    def test_small_gain_is_not_enough(self):
        assert select_champion(self.base, {"auroc": 0.73, "brier": 0.059}) == "baseline"

    def test_worse_calibration_blocks_challenger(self):
        assert select_champion(self.base, {"auroc": 0.80, "brier": 0.061}) == "baseline"

    def test_clear_winner_is_selected(self):
        assert select_champion(self.base, {"auroc": 0.80, "brier": 0.055}) == "challenger"

    def test_missing_metrics_keep_baseline(self):
        assert select_champion(self.base, {}) == "baseline"
        assert select_champion(self.base, None) == "baseline"


class TestTiers:
    def test_tiers_are_relative_to_base_rate(self):
        assert risk_tier(0.10, 0.03) == "high"
        assert risk_tier(0.05, 0.03) == "medium"
        assert risk_tier(0.03, 0.03) == "low"
        assert risk_tier(0.5, 0.0) == "low"


class TestAnchors:
    def test_anchor_dates_need_elapsed_window_and_spacing(self):
        logged = [T - timedelta(days=k) for k in range(0, 200)]
        got = anchor_dates(logged, T, horizon_days=90, step_days=14)
        assert got[0] == T - timedelta(days=90)
        assert all((a - b).days >= 14 for a, b in zip(got, got[1:], strict=False))


class TestPeerExposure:
    edges = [("a", "b", 3.0), ("b", "a", 1.0), ("a", "c", 4.0), ("c", "d", 1.0)]

    def test_tie_shares_are_undirected_and_row_normalised(self):
        ids, S = tie_share_matrix(self.edges)
        S = S.toarray()
        a, b, c = ids.index("a"), ids.index("b"), ids.index("c")
        assert S[a, b] == pytest.approx(0.5) and S[a, c] == pytest.approx(0.5)
        assert S[b, a] == pytest.approx(1.0)
        assert np.allclose(S.sum(axis=1), 1.0)

    def test_decay_and_window(self):
        ids = ["x", "y", "z"]
        deps = {"x": T, "y": T - timedelta(days=45), "z": T - timedelta(days=200)}
        assert departure_weights(ids, deps, T, window_days=90, half_life_days=45).tolist() == pytest.approx(
            [1.0, 0.5, 0.0]
        )

    def test_exposure_counts_departed_peers_and_excludes_the_departed(self):
        E = peer_exposure(self.edges, {"b": T}, T)
        assert "b" not in E
        assert E["a"] == pytest.approx(0.5)
        assert E["c"] == pytest.approx(0.0)

    def test_no_departures_no_flags(self):
        res = select_peer_exposure(self.edges, {}, T)
        assert all(not r.flagged and r.p_value == 1.0 for r in res.values())


class TestStats:
    def test_benjamini_hochberg(self):
        assert benjamini_hochberg([0.001, 0.02, 0.04, 0.5], 0.10).tolist() == [True, True, True, False]
        assert benjamini_hochberg([], 0.1).tolist() == []

    def test_permutation_pvalue_never_zero(self):
        assert permutation_pvalue(10.0, [0, 1, 2]) == pytest.approx(0.25)
        assert permutation_pvalue(-1.0, [0, 1, 2], greater=False) == pytest.approx(0.25)
