"""Sudden-withdrawal detection: single change-point in an employee's daily activity rate.

The entropy trend (feature_extractor) is deliberately smoothed and shrunk, so it
is slow to react to an abrupt stop — exactly the "quiet quitting overnight" or
pre-resignation dropout pattern HR most wants to see early.  This module looks
for that pattern directly.

Model (MATH_MODEL.md §4.2)
──────────────────────────
Daily initiated interactions c_1 … c_T are Poisson.  For every split day τ the
likelihood-ratio statistic for "rate λ₁ before, λ₂ after" against "one rate λ₀":

    2·LLR(τ) = 2 [ A·ln(λ₁/λ₀) + B·ln(λ₂/λ₀) ],   A = Σ_{t<τ} c_t,  B = Σ_{t≥τ} c_t,
               λ₁ = A/τ,  λ₂ = B/(T−τ),  λ₀ = (A+B)/T

Only drops (λ₂ < λ₁) are considered and τ is restricted so both segments have
≥ CHANGEPOINT_MIN_SEGMENT days.  A sudden withdrawal is declared when

    max_τ 2·LLR(τ) ≥ CHANGEPOINT_THRESHOLD           (default 13.8)
    λ₂ / λ₁        ≤ 1 − CHANGEPOINT_MIN_DROP         (default: at least a 50 % drop)
    λ₁             ≥ CHANGEPOINT_MIN_RATE             (default 1 interaction/day before)

Calibration: the maximum over splits is not χ²₁-distributed, so the threshold
was set by simulation on stable Poisson series (28 days, rates 1–8/day): the
99.5th percentile of max 2·LLR is ≈ 11 at every rate, and 13.8 gives ≈ 0.1 %
false alarms per employee-run.  tests/validation/entropy_trend_evaluator.py
re-measures false-alarm rate and power on every test run.

Activity counts only interactions the employee *initiated* (source side):
inbound messages keep arriving after someone withdraws.

Public functions:
    detect_rate_drop(daily_counts, ...) → RateDrop | None          (pure)
    load_daily_activity(snapshot_date, days, cur) → {id: np.ndarray}
    compute_sudden_withdrawals(snapshot_date, days) → {id: RateDrop}
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

CHANGEPOINT_THRESHOLD = float(os.environ.get("CHANGEPOINT_THRESHOLD", "13.8"))
CHANGEPOINT_MIN_DROP = float(os.environ.get("CHANGEPOINT_MIN_DROP", "0.5"))
CHANGEPOINT_MIN_RATE = float(os.environ.get("CHANGEPOINT_MIN_RATE", "1.0"))
CHANGEPOINT_MIN_SEGMENT = int(os.environ.get("CHANGEPOINT_MIN_SEGMENT", "5"))
CHANGEPOINT_WINDOW_DAYS = int(os.environ.get("CHANGEPOINT_WINDOW_DAYS", "28"))


@dataclass(frozen=True)
class RateDrop:
    """A detected drop in daily activity rate."""

    change_index: int  # first day (0-based within the series) of the lower-rate segment
    days_since_change: int  # T − change_index: how long the lower rate has lasted
    rate_before: float  # λ₁, interactions / day
    rate_after: float  # λ₂
    drop: float  # 1 − λ₂/λ₁ ∈ (0, 1]
    statistic: float  # max 2·LLR


def _xlogy(x: float, y: float) -> float:
    return x * math.log(y) if x > 0 else 0.0


def detect_rate_drop(
    daily_counts,
    threshold: float = CHANGEPOINT_THRESHOLD,
    min_drop: float = CHANGEPOINT_MIN_DROP,
    min_rate: float = CHANGEPOINT_MIN_RATE,
    min_segment: int = CHANGEPOINT_MIN_SEGMENT,
) -> RateDrop | None:
    """Return the strongest significant downward rate change, or None.

    Args:
        daily_counts: Interactions per day, oldest first.
    """
    c = np.asarray(daily_counts, dtype=float)
    t_total = len(c)
    total = float(c.sum())
    if t_total < 2 * min_segment or total <= 0:
        return None
    lam0 = total / t_total
    prefix = np.concatenate([[0.0], np.cumsum(c)])

    best: tuple[float, int, float, float] | None = None
    for tau in range(min_segment, t_total - min_segment + 1):
        a = float(prefix[tau])
        b = total - a
        lam1 = a / tau
        lam2 = b / (t_total - tau)
        if lam2 >= lam1:
            continue
        stat = 2.0 * (_xlogy(a, lam1 / lam0) + _xlogy(b, lam2 / lam0))
        if best is None or stat > best[0]:
            best = (stat, tau, lam1, lam2)

    if best is None:
        return None
    stat, tau, lam1, lam2 = best
    drop = 1.0 - lam2 / lam1
    if stat < threshold or drop < min_drop or lam1 < min_rate:
        return None
    return RateDrop(
        change_index=tau,
        days_since_change=t_total - tau,
        rate_before=round(lam1, 4),
        rate_after=round(lam2, 4),
        drop=round(drop, 4),
        statistic=round(stat, 3),
    )


def load_daily_activity(snapshot_date: date, days: int, cur) -> dict[str, np.ndarray]:
    """Initiated interactions per day for the *days* days ending on snapshot_date.

    Consent/active filters mirror graph.builder.load_raw_edges.  Every consenting
    active employee gets a series (all zeros if silent), so a complete stop is
    visible rather than missing.
    """
    start = snapshot_date - timedelta(days=days - 1)
    cur.execute(
        """
        SELECT re.source_id::text, (re.ts::date - %s::date) AS day_idx, COUNT(*) AS cnt
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
    series: dict[str, np.ndarray] = {r[0]: np.zeros(days) for r in cur.fetchall()}
    for emp_id, day_idx, cnt in rows:
        if emp_id in series and 0 <= int(day_idx) < days:
            series[emp_id][int(day_idx)] = float(cnt)
    return series


def compute_sudden_withdrawals(
    snapshot_date: date,
    days: int = CHANGEPOINT_WINDOW_DAYS,
) -> dict[str, RateDrop]:
    """Run detect_rate_drop for every employee; returns only those with a detected drop."""
    from ingestion.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            series = load_daily_activity(snapshot_date, days, cur)
    out: dict[str, RateDrop] = {}
    for emp_id, counts in series.items():
        hit = detect_rate_drop(counts)
        if hit is not None:
            out[emp_id] = hit
    return out
