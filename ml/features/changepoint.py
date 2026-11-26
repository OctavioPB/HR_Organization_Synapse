"""Sudden-withdrawal detection: a person's recent activity vs. their own history.

The entropy trend (feature_extractor) is deliberately smoothed and shrunk, so it
reacts slowly to an abrupt stop.  This module tests that pattern directly.

Model (MATH_MODEL.md §4.2)
──────────────────────────
Weekly initiated interactions over the last B + R weeks are split into a
personal *baseline* (first B weeks) and a *recent* window (last R weeks).  The
baseline is fitted with a negative binomial by method of moments:

    m = mean(baseline weeks),   v = max(var(baseline weeks), m)   (≥ Poisson)
    one week ~ NB(r, p),        r = m² / (v − m),   p = r / (r + m)
    R recent weeks ~ NB(R·r, p)

Sudden withdrawal is declared when

    P(NB(R·r, p) ≤ recent total) < SUDDEN_WITHDRAWAL_ALPHA      (default 0.001)
    recent weekly rate ≤ (1 − SUDDEN_WITHDRAWAL_MIN_DROP) · m   (default: ≥ 50 % drop)
    m ≥ SUDDEN_WITHDRAWAL_MIN_WEEKLY                            (default 7 / week)

Why this design and not a Poisson change-point on daily counts (the first
version): on real e-mail (SNAP email-Eu-core-temporal, 803 days) daily activity
has a strong weekly cycle (weekends ≈ 0.1× weekdays) and is overdispersed
(variance ≈ 5× the mean), and a Poisson daily change-point false-alarmed on
32–43 % of order-shuffled real windows instead of the simulated 0.05 %.  Weekly
totals remove the weekday cycle, and the person's own baseline variance makes
bursty people need more evidence.  On the same real-data null this test
false-alarms on ≈1.5 % (tests/validation/real_network_evaluator.py).

Cost of honesty: individual e-mail volume is noisy, so power on real histories
is modest — ≈22 % for a sustained 70 % drop and ≈53 % for a 90 % drop over three
weeks — rising with how active the person is.  Activity counts only
interactions the employee *initiated*: inbound messages keep arriving after
someone withdraws.  B + R = 12 weeks fits inside the 90-day raw_events retention.

Alert-volume control: the per-person test runs on every employee every day, so
raw p-values would produce false alerts in proportion to headcount — and on real
e-mail they are also ~15× too small.  select_sudden_withdrawals calibrates them
against a pooled null of shuffled real histories and applies Benjamini–Hochberg
false-discovery-rate control across employees (SUDDEN_WITHDRAWAL_FDR = 0.10).

Public functions:
    nb_cdf(k, r, p) → float                                           (pure)
    activity_drop_pvalue(weekly_counts, ...) → (p, before, after) | None
    detect_activity_drop(weekly_counts, ...) → ActivityDrop | None    (pure, single person)
    benjamini_hochberg(p_values, q) → bool mask                        (pure)
    select_sudden_withdrawals(series, fdr, ...) → {id: ActivityDrop}   (pure, population)
    load_weekly_activity(snapshot_date, weeks, cur) → {id: np.ndarray}
    compute_sudden_withdrawals(snapshot_date) → {id: ActivityDrop}
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

SUDDEN_WITHDRAWAL_ALPHA = float(os.environ.get("SUDDEN_WITHDRAWAL_ALPHA", "0.001"))
SUDDEN_WITHDRAWAL_MIN_DROP = float(os.environ.get("SUDDEN_WITHDRAWAL_MIN_DROP", "0.5"))
SUDDEN_WITHDRAWAL_MIN_WEEKLY = float(os.environ.get("SUDDEN_WITHDRAWAL_MIN_WEEKLY", "7"))
SUDDEN_WITHDRAWAL_BASELINE_WEEKS = int(os.environ.get("SUDDEN_WITHDRAWAL_BASELINE_WEEKS", "9"))
SUDDEN_WITHDRAWAL_RECENT_WEEKS = int(os.environ.get("SUDDEN_WITHDRAWAL_RECENT_WEEKS", "3"))
# Population-level alert control: false-discovery rate and total null draws
# used to calibrate p-values against shuffled real histories.
SUDDEN_WITHDRAWAL_FDR = float(os.environ.get("SUDDEN_WITHDRAWAL_FDR", "0.10"))
SUDDEN_WITHDRAWAL_NULL_DRAWS = int(os.environ.get("SUDDEN_WITHDRAWAL_NULL_DRAWS", "20000"))


@dataclass(frozen=True)
class ActivityDrop:
    """A recent activity level implausibly low given the person's own history."""

    weekly_before: float  # baseline mean interactions / week
    weekly_after: float  # recent mean interactions / week
    drop: float  # 1 − after/before ∈ (0, 1]
    p_value: float  # P(recent total this low | baseline NB)
    recent_weeks: int  # length of the recent window (weeks)


def nb_cdf(k: int, r: float, p: float) -> float:
    """P(X ≤ k) for X ~ NB(r, p) (number of failures, success prob p), summed in log space."""
    if k < 0:
        return 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    lg_r = math.lgamma(r)
    total = 0.0
    for j in range(int(k) + 1):
        total += math.exp(math.lgamma(j + r) - lg_r - math.lgamma(j + 1) + r * log_p + j * log_q)
    return min(total, 1.0)


def activity_drop_pvalue(
    weekly_counts,
    baseline_weeks: int = SUDDEN_WITHDRAWAL_BASELINE_WEEKS,
    recent_weeks: int = SUDDEN_WITHDRAWAL_RECENT_WEEKS,
    min_drop: float = SUDDEN_WITHDRAWAL_MIN_DROP,
    min_weekly: float = SUDDEN_WITHDRAWAL_MIN_WEEKLY,
) -> tuple[float, float, float] | None:
    """NB tail p-value of the recent window, or None if the employee is ineligible.

    Ineligible: history too short, baseline below *min_weekly*, or a drop smaller
    than *min_drop*.  Returns (p_value, baseline weekly mean, recent weekly mean).
    """
    w = np.asarray(weekly_counts, dtype=float)
    if len(w) < baseline_weeks + recent_weeks or baseline_weeks < 2:
        return None
    w = w[-(baseline_weeks + recent_weeks) :]
    base, recent = w[:baseline_weeks], w[baseline_weeks:]
    m = float(base.mean())
    if m < min_weekly:
        return None
    after = float(recent.mean())
    if 1.0 - after / m < min_drop:
        return None
    v = max(float(base.var(ddof=1)), m * 1.0001)  # never tighter than Poisson
    r = m * m / (v - m)
    p = r / (r + m)
    return nb_cdf(int(round(recent.sum())), recent_weeks * r, p), m, after


def detect_activity_drop(
    weekly_counts,
    baseline_weeks: int = SUDDEN_WITHDRAWAL_BASELINE_WEEKS,
    recent_weeks: int = SUDDEN_WITHDRAWAL_RECENT_WEEKS,
    alpha: float = SUDDEN_WITHDRAWAL_ALPHA,
    min_drop: float = SUDDEN_WITHDRAWAL_MIN_DROP,
    min_weekly: float = SUDDEN_WITHDRAWAL_MIN_WEEKLY,
) -> ActivityDrop | None:
    """Single-person test: last *recent_weeks* vs. the preceding *baseline_weeks*.

    Uses the raw NB p-value at level *alpha*.  The production path
    (select_sudden_withdrawals) instead calibrates p-values against a real-data
    null and controls the false-discovery rate across employees, because raw NB
    p-values are ~15× too small on real e-mail (a 9-week baseline under-states
    true variability).
    """
    res = activity_drop_pvalue(weekly_counts, baseline_weeks, recent_weeks, min_drop, min_weekly)
    if res is None or res[0] >= alpha:
        return None
    p_value, m, after = res
    return ActivityDrop(
        weekly_before=round(m, 3),
        weekly_after=round(after, 3),
        drop=round(1.0 - after / m, 4),
        p_value=p_value,
        recent_weeks=recent_weeks,
    )


def benjamini_hochberg(p_values, q: float) -> np.ndarray:
    """Boolean mask of discoveries controlling the false-discovery rate at *q*."""
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    if n == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    passed = p[order] <= q * np.arange(1, n + 1) / n
    hits = np.zeros(n, dtype=bool)
    if passed.any():
        hits[order[: int(np.max(np.where(passed)[0])) + 1]] = True
    return hits


def select_sudden_withdrawals(
    series: dict[str, np.ndarray],
    fdr: float = SUDDEN_WITHDRAWAL_FDR,
    null_draws: int = SUDDEN_WITHDRAWAL_NULL_DRAWS,
    seed: int = 0,
) -> dict[str, ActivityDrop]:
    """Population-level selection: calibrated p-values + Benjamini–Hochberg.

    1. Observed p-value per eligible employee (activity_drop_pvalue; ineligible = 1).
    2. Null: every employee's weekly history shuffled (no real drop, real
       burstiness kept) ≈ *null_draws* times in total, pooled.
    3. Calibrated p = (1 + #{null p ≤ observed}) / (1 + #null).
    4. Benjamini–Hochberg at *fdr* across employees.

    On real e-mail (SNAP email-Eu-core-temporal, ~117 eligible people per run)
    this cut false alerts on shuffled histories from 1.4–1.7 per run (raw
    α = 0.001) to 0.00, while still catching 38 % of injected 90 % drops.
    ``p_value`` on the returned ActivityDrop is the calibrated value.
    """
    ids = list(series)
    if not ids:
        return {}
    rng = np.random.default_rng(seed)
    observed: list[float] = []
    info: dict[str, tuple[float, float]] = {}
    for emp_id in ids:
        res = activity_drop_pvalue(series[emp_id])
        observed.append(1.0 if res is None else res[0])
        if res is not None:
            info[emp_id] = (res[1], res[2])
    if not info:
        return {}
    per_person = max(20, -(-null_draws // len(ids)))  # ceil
    null = []
    for emp_id in ids:
        w = np.asarray(series[emp_id], dtype=float)
        for _ in range(per_person):
            res = activity_drop_pvalue(rng.permutation(w))
            null.append(1.0 if res is None else res[0])
    null_sorted = np.sort(np.asarray(null))
    obs = np.asarray(observed)
    calibrated = (1 + np.searchsorted(null_sorted, obs, side="right")) / (1 + len(null_sorted))
    hits = benjamini_hochberg(calibrated, fdr)
    out: dict[str, ActivityDrop] = {}
    for i, emp_id in enumerate(ids):
        if hits[i] and emp_id in info:
            m, after = info[emp_id]
            out[emp_id] = ActivityDrop(
                weekly_before=round(m, 3),
                weekly_after=round(after, 3),
                drop=round(1.0 - after / m, 4),
                p_value=float(calibrated[i]),
                recent_weeks=SUDDEN_WITHDRAWAL_RECENT_WEEKS,
            )
    return out


def load_weekly_activity(snapshot_date: date, weeks: int, cur) -> dict[str, np.ndarray]:
    """Initiated interactions per 7-day week, oldest first, ending on snapshot_date.

    Consent/active filters mirror graph.builder.load_raw_edges.  Every consenting
    active employee gets a series (zeros if silent), so a complete stop is visible.
    """
    start = snapshot_date - timedelta(days=7 * weeks - 1)
    cur.execute(
        """
        SELECT re.source_id::text, (re.ts::date - %s::date) / 7 AS week_idx, COUNT(*) AS cnt
        FROM raw_events re
        JOIN employees es ON es.id = re.source_id
        JOIN employees et ON et.id = re.target_id
        WHERE re.ts::date BETWEEN %s::date AND %s::date
          AND es.consent = true AND et.consent = true
          AND es.active  = true AND et.active  = true
        GROUP BY 1, 2
        """,
        (start, start, snapshot_date),
    )
    rows = cur.fetchall()
    cur.execute("SELECT id::text FROM employees WHERE consent = true AND active = true")
    series: dict[str, np.ndarray] = {r[0]: np.zeros(weeks) for r in cur.fetchall()}
    for emp_id, week_idx, cnt in rows:
        if emp_id in series and 0 <= int(week_idx) < weeks:
            series[emp_id][int(week_idx)] = float(cnt)
    return series


def compute_sudden_withdrawals(snapshot_date: date) -> dict[str, ActivityDrop]:
    """Run detect_activity_drop for every employee; returns only those with a detected drop."""
    from ingestion.db import get_conn

    weeks = SUDDEN_WITHDRAWAL_BASELINE_WEEKS + SUDDEN_WITHDRAWAL_RECENT_WEEKS
    with get_conn() as conn:
        with conn.cursor() as cur:
            series = load_weekly_activity(snapshot_date, weeks, cur)
    return select_sudden_withdrawals(series)
