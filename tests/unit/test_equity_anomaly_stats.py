"""Unit tests: equity permutation tests (graph/equity_stats.py) and conformal anomalies."""

from __future__ import annotations

import numpy as np
import pytest

from graph.equity_stats import apply_fdr, group_tests
from ml.anomaly.isolation_forest import conformal_anomalies, detect_anomalies, feature_matrix
from ml.stats import benjamini_hochberg, bh_adjusted


class TestEquityStats:
    def _org(self, gap: float, seed: int = 0):
        rng = np.random.default_rng(seed)
        groups = np.array(["a"] * 60 + ["b"] * 60 + ["tiny"] * 3)
        x = rng.lognormal(2.0, 0.5, len(groups))
        x[groups == "a"] *= 1 - gap
        return x, groups

    def test_small_groups_never_reported(self):
        x, g = self._org(0.0)
        assert {t.group for t in group_tests(x, g, "d", "degree", min_group_size=5, n_perm=200)} == {"a", "b"}

    def test_reference_must_be_k_anonymous(self):
        x = np.arange(10.0)
        g = np.array(["big"] * 7 + ["other"] * 3)
        assert group_tests(x, g, "d", "m", min_group_size=5, n_perm=50) == []

    def test_planted_gap_flagged_and_parity_not(self):
        x, g = self._org(0.4)
        tests = apply_fdr(group_tests(x, g, "d", "degree", min_group_size=5, n_perm=2000))
        by = {t.group: t for t in tests}
        assert by["a"].flagged and by["a"].mean_ratio < 0.8 and by["a"].p_value < 0.01
        assert not by["b"].flagged
        assert by["a"].effect_size < 0.5 < by["b"].effect_size

    def test_significant_but_small_gap_is_not_flagged(self):
        rng = np.random.default_rng(1)
        g = np.array(["a"] * 2000 + ["b"] * 2000)
        x = rng.normal(10, 1, 4000)
        x[g == "a"] *= 0.9  # clearly significant, but only a 10 % gap
        tests = apply_fdr(group_tests(x, g, "d", "m", min_group_size=5, n_perm=500))
        a = next(t for t in tests if t.group == "a")
        assert a.q_value < 0.01 and not a.flagged

    def test_zero_median_metric_still_testable(self):
        rng = np.random.default_rng(2)
        g = np.array(["a"] * 150 + ["b"] * 150)
        x = np.where(rng.random(300) < 0.6, 0.0, rng.exponential(1.0, 300))
        x[g == "a"] *= 0.3
        tests = apply_fdr(group_tests(x, g, "d", "betweenness", min_group_size=5, n_perm=2000))
        assert next(t for t in tests if t.group == "a").flagged


class TestBH:
    def test_adjusted_matches_step_up(self):
        p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216])
        for level in (0.05, 0.1, 0.25):
            assert ((bh_adjusted(p) <= level) == benjamini_hochberg(p, level)).all()


class TestConformalAnomalies:
    def _ref(self, n=2000, seed=0):
        return np.random.default_rng(seed).normal(0, 1, (n, 8))

    def test_null_p_values_are_roughly_uniform(self):
        today = np.random.default_rng(9).normal(0, 1, (2000, 8))
        _, p, _, flagged = conformal_anomalies(today, self._ref())
        assert abs(np.mean(p <= 0.1) - 0.1) < 0.03
        assert flagged.sum() <= 2

    def test_extreme_point_is_flagged(self):
        today = np.vstack([np.random.default_rng(3).normal(0, 1, (100, 8)), np.full((1, 8), 8.0)])
        score, p, q, flagged = conformal_anomalies(today, self._ref())
        assert flagged[-1] and flagged[:-1].sum() <= 1
        assert score[-1] == pytest.approx(1 - p[-1])

    def test_too_little_history_flags_nothing(self):
        _, p, _, flagged = conformal_anomalies(np.zeros((5, 8)), np.zeros((3, 8)))
        assert not flagged.any() and (p == 1).all()

    def test_feature_matrix_clips_like_the_feature_log(self):
        X = feature_matrix(
            [{"employee_id": "x", "betweenness_delta_7d": 5.0, "entropy_current": 3.0, "entropy_trend": -2}]
        )
        assert X[0, 4] == 1.0 and X[0, 6] == 1.0 and X[0, 7] == -1.0

    def test_detect_anomalies_shape(self):
        feats = [{"employee_id": f"e{i}"} for i in range(3)]
        out = detect_anomalies(feats, self._ref(200)[:, :8])
        assert [r["employee_id"] for r in out] == ["e0", "e1", "e2"]
        assert {"p_value", "q_value", "severity", "is_anomaly"} <= set(out[0])
