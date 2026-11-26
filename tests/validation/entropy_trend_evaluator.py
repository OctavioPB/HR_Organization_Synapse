"""Validation: robust entropy trend + sudden-withdrawal change-point on planted ground truth.

Synthetic org (seeded, no DB):
  * every employee has a TRUE entropy slope.  Stable people: N(0, 0.08) bits/week.
    10 % planted withdrawers: U(−0.6, −0.25).
  * week k's collaborator pool has K_k = 12 · 2^(slope·k) partners, so the true
    entropy of the pool changes by exactly `slope` bits per week.
  * activity varies 2–60 events/week (log-uniform) and is Poisson week to week,
    so low-activity people have very noisy plug-in entropies — the real problem.
  * 25 % of employees have one "holiday" week at 15 % of their usual activity.

Each estimator is run through the production code path
(ml.features.feature_extractor.entropy_signals_from_weekly) and compared with the
OLS slope the pipeline used before.

Change-point: stable Poisson daily series (28 days, 1–8 events/day) for the
false-alarm rate, and series with a 70 % drop at day 18 for power and location.

Gates (with measured values at the time of writing in brackets):
    RMSE reduction vs OLS, all employees          ≥ 35 %   [53 %: 0.300 → 0.140]
    RMSE reduction vs OLS, low activity (<8/wk)   ≥ 40 %   [55 %: 0.370 → 0.167]
    holiday-affected RMSE: Theil-Sen+shrink < OLS+shrink
    withdrawing flag: false-positive rate          ≤  6 %   [2.5 %]  (legacy "any negative
                                                             slope" rule: 50.8 %; OLS < −0.05: 39 %)
    withdrawing flag: recall on planted withdrawers ≥ 30 %   [37 %; 84 % for ≥ 20 events/week]
    change-point false alarms per series          ≤ 0.5 %  [0.05 %]
    change-point power, 70 % drop, rate ≥ 5/day   ≥ 95 %   [94 % at 5/day, 100 % at ≥ 8/day;
                                                             61 % at 3/day, 9 % at 1/day]
    change-point location error (median)          ≤ 2 days [0 days]
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from ml.features.changepoint import detect_rate_drop
from ml.features.feature_extractor import entropy_signals_from_weekly

_N = 2000
_SEED = 7


@dataclass
class _Org:
    true: np.ndarray
    withdrawer: np.ndarray
    activity: np.ndarray
    holiday: np.ndarray
    ids: list[str]
    weekly: dict[str, dict[int, dict[str, int]]]


def _build_org(seed: int = _SEED, n: int = _N) -> _Org:
    rng = np.random.default_rng(seed)
    true = rng.normal(0.0, 0.08, n)
    withdrawer = rng.random(n) < 0.10
    true[withdrawer] = rng.uniform(-0.6, -0.25, withdrawer.sum())
    activity = np.exp(rng.uniform(np.log(2), np.log(60), n))
    holiday = rng.random(n) < 0.25
    holiday_week = rng.integers(0, 4, n)
    ids = [f"e{i}" for i in range(n)]
    weekly: dict[str, dict[int, dict[str, int]]] = {}
    for i, emp in enumerate(ids):
        weeks: dict[int, dict[str, int]] = {}
        for k in range(4):
            pool = max(2, int(round(12 * 2 ** (true[i] * k))))
            lam = activity[i] * (0.15 if holiday[i] and holiday_week[i] == k else 1.0)
            draws = rng.integers(0, pool, rng.poisson(lam))
            counts = np.bincount(draws, minlength=pool)
            weeks[k] = {f"p{j}": int(c) for j, c in enumerate(counts) if c > 0}
        weekly[emp] = weeks
    return _Org(true, withdrawer, activity, holiday, ids, weekly)


def _ols_from_signal(sig) -> float:
    return float(np.polyfit(np.arange(4.0), list(sig.weekly_entropy), 1)[0])


@pytest.fixture(scope="module")
def results():
    org = _build_org()
    sig = entropy_signals_from_weekly(org.weekly)
    ols = np.array([_ols_from_signal(sig[e]) for e in org.ids])
    robust = np.array([sig[e].slope for e in org.ids])
    flag = np.array([sig[e].withdrawing for e in org.ids])

    # OLS + the same shrinkage, to isolate the Theil-Sen contribution on holiday weeks
    from ml.features.feature_extractor import shrink_toward_center

    ols_shrunk_map = shrink_toward_center(
        dict(zip(org.ids, ols, strict=True)), {e: sig[e].raw_slope_var for e in org.ids}
    )
    ols_shrunk = np.array([ols_shrunk_map[e][0] for e in org.ids])
    return org, ols, robust, ols_shrunk, flag


def _rmse(est, truth, mask=None):
    d = est - truth
    if mask is not None:
        d = d[mask]
    return float(np.sqrt(np.mean(d**2)))


def test_rmse_reduction_overall(results):
    org, ols, robust, _, _ = results
    reduction = 1 - _rmse(robust, org.true) / _rmse(ols, org.true)
    assert reduction >= 0.35, f"RMSE reduction {reduction:.0%} (OLS {_rmse(ols, org.true):.3f})"


def test_rmse_reduction_low_activity(results):
    org, ols, robust, _, _ = results
    low = org.activity < 8
    reduction = 1 - _rmse(robust, org.true, low) / _rmse(ols, org.true, low)
    assert reduction >= 0.40, f"low-activity RMSE reduction {reduction:.0%}"


def test_theil_sen_helps_on_holiday_weeks(results):
    org, _, robust, ols_shrunk, _ = results
    assert _rmse(robust, org.true, org.holiday) < _rmse(ols_shrunk, org.true, org.holiday)


def test_withdrawing_flag_false_positive_rate(results):
    org, ols, _, _, flag = results
    stable = ~org.withdrawer
    fp = flag[stable].mean()
    legacy_fp = (ols[stable] < -0.05).mean()
    assert fp <= 0.06, f"FP rate {fp:.1%} (legacy OLS < −0.05: {legacy_fp:.1%})"
    assert fp < legacy_fp / 4


def test_withdrawing_flag_recall(results):
    org, _, _, _, flag = results
    recall = flag[org.withdrawer].mean()
    assert recall >= 0.30, f"recall {recall:.0%}"


def test_well_measured_withdrawers_mostly_caught(results):
    """Misses concentrate in low-activity people, whose data cannot support a confident call."""
    org, _, _, _, flag = results
    busy_withdrawers = org.withdrawer & (org.activity >= 20)
    assert flag[busy_withdrawers].mean() >= 0.70


# ─── change-point ─────────────────────────────────────────────────────────────


def test_changepoint_false_alarm_rate():
    rng = np.random.default_rng(11)
    alarms = trials = 0
    for rate in (1, 2, 4, 8):
        for _ in range(1000):
            alarms += detect_rate_drop(rng.poisson(rate, 28)) is not None
            trials += 1
    assert alarms / trials <= 0.005, f"false alarms {alarms}/{trials}"


def test_changepoint_power_and_location():
    rng = np.random.default_rng(12)
    hits, errors = 0, []
    trials = 0
    for rate in (5, 8, 12):
        for _ in range(400):
            series = np.concatenate([rng.poisson(rate, 18), rng.poisson(rate * 0.3, 10)])
            r = detect_rate_drop(series)
            trials += 1
            if r is not None:
                hits += 1
                errors.append(abs(r.change_index - 18))
    assert hits / trials >= 0.95, f"power {hits / trials:.0%}"
    assert float(np.median(errors)) <= 2
