"""Unit tests for the robust entropy trend and the sudden-withdrawal detector.

Statistical performance (error reduction, false-positive rate, power) is measured
on planted ground truth in tests/validation/entropy_trend_evaluator.py; these
tests pin the mechanics.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ml.features.changepoint import benjamini_hochberg, detect_activity_drop, nb_cdf, select_sudden_withdrawals
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


# ─── extra week-to-week variance ──────────────────────────────────────────────


class TestExtraWeekVariance:
    def test_zero_when_scatter_matches_sampling_noise(self):
        from ml.features.feature_extractor import estimate_extra_week_variance

        rng = np.random.default_rng(0)
        h = {f"e{i}": list(3 + rng.normal(0, 0.1, 4)) for i in range(50)}
        v = {k: [0.01] * 4 for k in h}  # sampling variance = true scatter (0.1²)
        assert estimate_extra_week_variance(h, v) < 0.01

    def test_detects_jitter_beyond_sampling(self):
        from ml.features.feature_extractor import estimate_extra_week_variance

        rng = np.random.default_rng(1)
        h = {f"e{i}": list(3 + rng.normal(0, 0.6, 4)) for i in range(200)}  # true scatter 0.36
        v = {k: [0.01] * 4 for k in h}
        assert 0.15 < estimate_extra_week_variance(h, v) < 0.5

    def test_robust_to_a_few_erratic_people(self):
        from ml.features.feature_extractor import estimate_extra_week_variance

        rng = np.random.default_rng(2)
        h = {f"e{i}": list(3 + rng.normal(0, 0.1, 4)) for i in range(60)}
        h.update({f"wild{i}": [0.0, 6.0, 0.0, 6.0] for i in range(5)})
        v = {k: [0.01] * 4 for k in h}
        assert estimate_extra_week_variance(h, v) < 0.02

    def test_small_population_returns_zero(self):
        from ml.features.feature_extractor import estimate_extra_week_variance

        assert estimate_extra_week_variance({"a": [0, 5, 0, 5]}, {"a": [0.01] * 4}) == 0.0


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


# ─── sudden withdrawal: recent vs. personal baseline ─────────────────────────


class TestNbCdf:
    def test_matches_scipy(self):
        scipy_stats = pytest.importorskip("scipy.stats")
        for k, r, p in [(0, 2.0, 0.4), (5, 3.5, 0.3), (40, 12.0, 0.25), (3, 0.7, 0.6)]:
            assert nb_cdf(k, r, p) == pytest.approx(scipy_stats.nbinom.cdf(k, r, p), rel=1e-9)

    def test_negative_k(self):
        assert nb_cdf(-1, 2.0, 0.5) == 0.0


class TestActivityDrop:
    def test_sustained_drop_detected(self):
        weeks = [30, 34, 28, 31, 33, 29, 32, 30, 35] + [4, 3, 2]
        hit = detect_activity_drop(weeks)
        assert hit is not None
        assert hit.weekly_before == pytest.approx(np.mean(weeks[:9]), rel=1e-3)
        assert hit.drop > 0.85
        assert hit.p_value < 0.001

    def test_bursty_baseline_needs_more_evidence(self):
        """Same means and the same recent dip; a volatile history makes it unremarkable."""
        steady = [30, 31, 29, 30, 32, 28, 30, 31, 29] + [12, 12, 12]
        bursty = [5, 70, 2, 60, 8, 55, 3, 48, 19] + [12, 12, 12]
        assert detect_activity_drop(steady) is not None
        assert detect_activity_drop(bursty) is None

    def test_small_dip_not_flagged(self):
        """A 30 % dip on a very steady person is significant but not a 'withdrawal'."""
        weeks = [100] * 9 + [70, 70, 70]
        assert detect_activity_drop(weeks) is None
        assert detect_activity_drop(weeks, min_drop=0.2) is not None

    def test_low_baseline_not_flagged(self):
        assert detect_activity_drop([4] * 9 + [0, 0, 0]) is None

    def test_uses_only_the_most_recent_window(self):
        old_drop_then_recovery = [30] * 3 + [2] * 3 + [30] * 9 + [31, 29, 30]
        assert detect_activity_drop(old_drop_then_recovery) is None

    def test_too_short_history(self):
        assert detect_activity_drop([30] * 5) is None


class TestAlertVolumeControl:
    def test_benjamini_hochberg_known_case(self):
        p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        # classic BH example at q=0.05: the first two are discoveries
        assert benjamini_hochberg(p, 0.05).tolist() == [True, True] + [False] * 8

    def test_benjamini_hochberg_empty_and_none(self):
        assert benjamini_hochberg([], 0.1).tolist() == []
        assert not benjamini_hochberg([0.5, 0.9], 0.1).any()

    def test_population_null_yields_no_alerts(self):
        """Stable but bursty people only: calibrated BH must stay quiet."""
        rng = np.random.default_rng(0)
        series = {}
        for i in range(150):
            mean = rng.uniform(10, 60)
            r = mean / 3.0
            series[f"e{i}"] = rng.negative_binomial(r, r / (r + mean), 12).astype(float)
        assert select_sudden_withdrawals(series, fdr=0.10, null_draws=6000) == {}

    def test_population_catches_clear_stops(self):
        rng = np.random.default_rng(1)
        series = {f"e{i}": rng.poisson(30, 12).astype(float) for i in range(150)}
        for i in range(5):
            series[f"stop{i}"] = np.array([30.0] * 9 + [0.0, 1.0, 0.0])
        hits = select_sudden_withdrawals(series, fdr=0.10, null_draws=6000)
        assert {f"stop{i}" for i in range(5)} <= set(hits)
        assert all(h.drop > 0.9 for k, h in hits.items() if k.startswith("stop"))
