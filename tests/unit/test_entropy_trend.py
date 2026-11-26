"""Unit tests for the robust entropy trend and the sudden-withdrawal change-point.

Statistical performance (error reduction, false-positive rate, power) is measured
on planted ground truth in tests/validation/entropy_trend_evaluator.py; these
tests pin the mechanics.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ml.features.changepoint import detect_rate_drop
from ml.features.feature_extractor import (
    compute_entropy,
    entropy_signals_from_weekly,
    entropy_with_variance,
    shrink_toward_center,
    slope_sampling_variance,
    theil_sen_slope,
)

# ─── entropy_with_variance ────────────────────────────────────────────────────


class TestEntropyWithVariance:
    def test_entropy_matches_plain_estimator(self):
        counts = {"a": 10, "b": 3, "c": 7}
        h, _ = entropy_with_variance(counts)
        assert h == pytest.approx(compute_entropy(counts))

    def test_variance_shrinks_with_more_events(self):
        _, v_small = entropy_with_variance({"a": 2, "b": 1, "c": 1})
        _, v_large = entropy_with_variance({"a": 200, "b": 100, "c": 100})
        assert v_large < v_small

    def test_floor_for_tiny_samples(self):
        """One event to one partner is not 'certain' entropy 0."""
        h, v = entropy_with_variance({"a": 1})
        assert h == 0.0
        assert v == pytest.approx(1.0)

    def test_empty_week(self):
        assert entropy_with_variance({}) == (0.0, 1.0)
        assert entropy_with_variance({"a": 0}) == (0.0, 1.0)


# ─── theil_sen_slope ──────────────────────────────────────────────────────────


class TestTheilSen:
    def test_linear_series_exact(self):
        assert theil_sen_slope([3.0, 2.5, 2.0, 1.5]) == pytest.approx(-0.5)

    def test_interior_outlier_week_is_ignored(self):
        """A holiday in a middle week: OLS invents a trend, Theil-Sen stays flat."""
        series = [3.0, 0.5, 3.0, 3.0]
        ols = float(np.polyfit(np.arange(4.0), series, 1)[0])
        assert abs(ols) > 0.2
        assert theil_sen_slope(series) == pytest.approx(0.0)

    def test_latest_week_outlier_is_damped_not_ignored(self):
        """A collapse in the most recent week looks like the start of real withdrawal,
        so with 4 points Theil-Sen only halves its effect (change-point detection on
        daily activity is what adjudicates that case)."""
        series = [3.0, 3.0, 3.0, 0.5]
        ols = float(np.polyfit(np.arange(4.0), series, 1)[0])
        ts = theil_sen_slope(series)
        assert ols < ts < 0
        assert abs(ts) < 0.6 * abs(ols)

    def test_degenerate(self):
        assert theil_sen_slope([]) == 0.0
        assert theil_sen_slope([2.0]) == 0.0


# ─── slope variance + shrinkage ───────────────────────────────────────────────


def test_slope_variance_formula():
    # x = 0..3 → w = (−1.5, −0.5, 0.5, 1.5), Σw² = 5 → Var = Σ w² v / 25
    assert slope_sampling_variance([1.0, 1.0, 1.0, 1.0]) == pytest.approx(5 / 25)
    assert slope_sampling_variance([0.0, 0.0, 0.0, 1.0]) == pytest.approx(2.25 / 25)


class TestShrinkage:
    def _population(self):
        rng = np.random.default_rng(0)
        slopes = {f"e{i}": float(rng.normal(0, 0.1)) for i in range(50)}
        variances = {k: 0.005 for k in slopes}
        return slopes, variances

    def test_noisy_estimate_pulled_harder_than_precise_one(self):
        slopes, variances = self._population()
        slopes["noisy"], variances["noisy"] = -0.8, 1.0
        slopes["precise"], variances["precise"] = -0.8, 0.0001
        out = shrink_toward_center(slopes, variances)
        m = float(np.median(list(slopes.values())))
        assert abs(out["noisy"][0] - m) < abs(out["precise"][0] - m)
        assert out["precise"][0] == pytest.approx(-0.8, abs=0.02)
        assert out["noisy"][2] < out["precise"][2]  # B: share of estimate kept

    def test_posterior_sd_below_sampling_sd(self):
        slopes, variances = self._population()
        out = shrink_toward_center(slopes, variances)
        assert all(sd <= math.sqrt(variances[k]) + 1e-12 for k, (_, sd, _) in out.items())

    def test_heterogeneous_noise_does_not_collapse_the_signal(self):
        """Regression: method-of-moments τ² went to ~0 when a few employees were very
        noisy, shrinking everyone onto the median.  DerSimonian–Laird must keep the
        real spread of the well-measured majority."""
        rng = np.random.default_rng(1)
        slopes = {f"e{i}": float(rng.normal(0, 0.15)) for i in range(60)}
        variances = {k: 0.001 for k in slopes}
        for i in range(8):  # a handful of near-silent employees
            slopes[f"quiet{i}"] = float(rng.normal(0, 1.5))
            variances[f"quiet{i}"] = 2.0
        out = shrink_toward_center(slopes, variances)
        kept = np.median([out[f"e{i}"][2] for i in range(60)])
        assert kept > 0.9
        assert max(out[f"quiet{i}"][2] for i in range(8)) < 0.05

    def test_too_few_employees_unshrunk(self):
        out = shrink_toward_center({"a": -0.3, "b": 0.1}, {"a": 0.5, "b": 0.5})
        assert out["a"][0] == -0.3 and out["a"][2] == 1.0


# ─── entropy_signals_from_weekly ──────────────────────────────────────────────


def _weekly(pattern_by_week: list[int], events_per_partner: int = 3) -> dict[int, dict[str, int]]:
    """Week k talks evenly to pattern_by_week[k] partners."""
    return {k: {f"p{j}": events_per_partner for j in range(n)} for k, n in enumerate(pattern_by_week)}


class TestEntropySignals:
    def _org(self):
        org = {f"stable{i}": _weekly([8, 8, 8, 8]) for i in range(30)}
        org["narrowing"] = _weekly([16, 8, 4, 2], events_per_partner=10)
        org["quiet_noise"] = {0: {"x": 1}, 1: {"y": 1, "z": 1}, 2: {}, 3: {"x": 1}}
        return entropy_signals_from_weekly(org)

    def test_well_measured_narrowing_is_flagged(self):
        s = self._org()["narrowing"]
        assert s.slope < -0.5
        assert s.withdrawing
        assert s.p_withdrawing > 0.9

    def test_low_activity_noise_is_not_flagged(self):
        s = self._org()["quiet_noise"]
        assert s.shrinkage < 0.5
        assert not s.withdrawing

    def test_stable_employees_not_flagged(self):
        sig = self._org()
        assert not any(sig[f"stable{i}"].withdrawing for i in range(30))

    def test_signal_carries_history(self):
        s = self._org()["narrowing"]
        assert s.weekly_events == (160, 80, 40, 20)
        assert len(s.weekly_entropy) == 4


# ─── change-point ─────────────────────────────────────────────────────────────


class TestRateDrop:
    def test_detects_abrupt_drop_and_locates_it(self):
        rng = np.random.default_rng(3)
        counts = np.concatenate([rng.poisson(8, 18), rng.poisson(1, 10)])
        hit = detect_rate_drop(counts)
        assert hit is not None
        assert abs(hit.change_index - 18) <= 2
        assert hit.drop > 0.7
        assert hit.days_since_change == 28 - hit.change_index

    def test_stable_series_not_flagged(self):
        rng = np.random.default_rng(4)
        assert detect_rate_drop(rng.poisson(6, 28)) is None

    def test_increase_not_flagged(self):
        rng = np.random.default_rng(5)
        assert detect_rate_drop(np.concatenate([rng.poisson(2, 14), rng.poisson(9, 14)])) is None

    def test_small_drop_not_flagged_even_if_significant(self):
        """A 25 % dip on a busy person is statistically real but not a 'withdrawal'."""
        counts = np.array([40] * 14 + [30] * 14)
        assert detect_rate_drop(counts) is None
        assert detect_rate_drop(counts, min_drop=0.2) is not None

    def test_low_baseline_not_flagged(self):
        counts = np.array([1] * 10 + [0] * 18)
        assert detect_rate_drop(counts, min_rate=1.5) is None

    def test_degenerate_inputs(self):
        assert detect_rate_drop([]) is None
        assert detect_rate_drop([0] * 28) is None
        assert detect_rate_drop([5] * 6) is None  # shorter than two segments
