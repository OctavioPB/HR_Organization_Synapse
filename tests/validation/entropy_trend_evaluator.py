"""Validation on SYNTHETIC ground truth: robust entropy trend + sudden-withdrawal detector.

Real-data counterparts (false-positive rates on real-data nulls, power on
withdrawals injected into real histories) are in real_network_evaluator.py —
synthetic results here were 5–600× too optimistic about false alarms until the
models were corrected against real e-mail.

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

Sudden withdrawal: 12-week weekly series from a negative binomial (variance 3×
the mean, i.e. overdispersed like real e-mail) for the false-alarm rate, and the
same with a 90 % drop in the last 3 weeks for power.

Gates (with measured values at the time of writing in brackets):
    RMSE reduction vs OLS, all employees          ≥ 35 %   [53 %: 0.300 → 0.140]
    RMSE reduction vs OLS, low activity (<8/wk)   ≥ 40 %   [55 %: 0.370 → 0.167]
    holiday-affected RMSE: Theil-Sen+shrink < OLS+shrink
    withdrawing flag: false-positive rate          ≤  6 %   [2.5 %]  (legacy "any negative
                                                             slope" rule: 50.8 %; OLS < −0.05: 39 %)
    withdrawing flag: recall on planted withdrawers ≥ 30 %   [37 %; 84 % for ≥ 20 events/week]
    sudden-withdrawal false alarms (NB null)       ≤ 0.5 %
    sudden-withdrawal power, 90 % drop, ≥ 25/week  ≥ 80 %
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from ml.features.changepoint import detect_activity_drop
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


# ─── sudden withdrawal (synthetic, overdispersed) ────────────────────────────
# Real-data false-alarm rate and power are measured in real_network_evaluator.py;
# here we pin behaviour on a controlled negative-binomial null (variance 3× mean).


def _nb_weeks(rng, mean, n, dispersion=3.0):
    r = mean / (dispersion - 1.0)
    return rng.negative_binomial(r, r / (r + mean), n).astype(float)


def test_sudden_withdrawal_false_alarm_rate_overdispersed():
    rng = np.random.default_rng(11)
    alarms = trials = 0
    for mean in (10, 25, 60):
        for _ in range(1500):
            alarms += detect_activity_drop(_nb_weeks(rng, mean, 12)) is not None
            trials += 1
    assert alarms / trials <= 0.005, f"false alarms {alarms}/{trials}"


def test_sudden_withdrawal_power_for_near_stop():
    rng = np.random.default_rng(12)
    hits = trials = 0
    for mean in (25, 60):
        for _ in range(500):
            w = _nb_weeks(rng, mean, 12)
            w[9:] = rng.binomial(w[9:].astype(int), 0.1)  # 90 % drop for 3 weeks
            hits += detect_activity_drop(w) is not None
            trials += 1
    assert hits / trials >= 0.80, f"power {hits / trials:.0%}"
