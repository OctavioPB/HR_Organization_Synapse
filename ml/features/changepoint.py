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

Public functions:
    nb_cdf(k, r, p) → float                                           (pure)
    detect_activity_drop(weekly_counts, ...) → ActivityDrop | None    (pure)
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


def detect_activity_drop(
    weekly_counts,
    baseline_weeks: int = SUDDEN_WITHDRAWAL_BASELINE_WEEKS,
    recent_weeks: int = SUDDEN_WITHDRAWAL_RECENT_WEEKS,
    alpha: float = SUDDEN_WITHDRAWAL_ALPHA,
    min_drop: float = SUDDEN_WITHDRAWAL_MIN_DROP,
    min_weekly: float = SUDDEN_WITHDRAWAL_MIN_WEEKLY,
) -> ActivityDrop | None:
    """Test the last *recent_weeks* against the preceding *baseline_weeks* (oldest first)."""
    w = np.asarray(weekly_counts, dtype=float)
    if len(w) < baseline_weeks + recent_weeks:
        return None
    w = w[-(baseline_weeks + recent_weeks) :]
    base, recent = w[:baseline_weeks], w[baseline_weeks:]
    m = float(base.mean())
    if m < min_weekly or baseline_weeks < 2:
        return None
    after = float(recent.mean())
    drop = 1.0 - after / m
    if drop < min_drop:
        return None
    v = max(float(base.var(ddof=1)), m * 1.0001)  # never tighter than Poisson
    r = m * m / (v - m)
    p = r / (r + m)
    p_value = nb_cdf(int(round(recent.sum())), recent_weeks * r, p)
    if p_value >= alpha:
        return None
    return ActivityDrop(
        weekly_before=round(m, 3),
        weekly_after=round(after, 3),
        drop=round(drop, 4),
        p_value=p_value,
        recent_weeks=recent_weeks,
    )


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
    out: dict[str, ActivityDrop] = {}
    for emp_id, counts in series.items():
        hit = detect_activity_drop(counts)
        if hit is not None:
            out[emp_id] = hit
    return out
