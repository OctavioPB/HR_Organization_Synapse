"""Rolling feature extraction from graph_snapshots and raw_events.

For each employee present in graph_snapshots on snapshot_date, we extract:
  - Current betweenness, degree_in, degree_out, clustering
  - 7-day deltas (vs prior snapshot)
  - Current-week interaction entropy
  - Entropy trend over 4 equal weeks: Theil-Sen slope, shrunk toward the
    population centre by its sampling variance (negative = withdrawing)

Pure computation functions (no DB, fully testable):
    compute_entropy(partner_counts) → float
    compute_entropy_trend(weekly_entropies) → float          (plain OLS; legacy)
    entropy_with_variance(partner_counts) → (H, Var(H))
    theil_sen_slope(values) → float
    slope_sampling_variance(point_variances) → float
    shrink_toward_center(slopes, variances) → {id: (mean, sd, B)}
    entropy_signals_from_weekly(weekly) → {id: EntropySignal}
    build_feature_vector(current, prev, entropy_current, entropy_trend) → dict

DB-accessing functions:
    compute_entropy_signals(snapshot_date, window_days) → dict[str, EntropySignal]
    compute_entropy_trends(snapshot_date, window_days)  → dict[str, float]  (signal.slope)
    extract_features(snapshot_date, window_days)        → list[dict]


CLI:
    python ml/features/feature_extractor.py --snapshot-date 2025-04-25
"""

import argparse
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.db import get_conn

logger = logging.getLogger(__name__)

# Number of weekly buckets used for entropy trend computation.
_N_WEEKS = 4


# ─── Pure computation ─────────────────────────────────────────────────────────


def compute_entropy(partner_counts: dict[str, int]) -> float:
    """Shannon entropy of the distribution of interaction partners.

    Args:
        partner_counts: Map from partner_id → number of interactions.

    Returns:
        H = -Σ p_i × log₂(p_i) in bits. Returns 0.0 for empty input or
        when all interactions go to the same partner.
    """
    if not partner_counts:
        return 0.0
    total = sum(partner_counts.values())
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in partner_counts.values() if c > 0)


def compute_entropy_trend(weekly_entropies: list[float]) -> float:
    """Linear regression slope of weekly entropy values (oldest first).

    Args:
        weekly_entropies: Entropy values, one per week, ascending in time.

    Returns:
        Slope of the best-fit line. Negative means declining diversity
        (withdrawing employee). Returns 0.0 for fewer than 2 data points.
    """
    if len(weekly_entropies) < 2:
        return 0.0
    x = np.arange(len(weekly_entropies), dtype=float)
    slope: float = float(np.polyfit(x, weekly_entropies, 1)[0])
    return slope


# ─── Robust, uncertainty-aware entropy trend (MATH_MODEL.md §4) ──────────────
#
# The raw signal is a slope through 4 weekly entropies.  Two problems:
#   1. one odd week (holiday, offsite, sick leave) swings an OLS slope;
#   2. entropy estimated from a handful of events is very noisy, so low-activity
#      people dominate both tails of the slope distribution.
# Fixes: Theil-Sen slope (median of pairwise slopes) for (1); an empirical-Bayes
# shrinkage of every slope toward the population centre, in proportion to its
# sampling variance, for (2).  "Withdrawing" then means we are confident the
# true slope is below the threshold, not merely that the noisy estimate is.

# Slope threshold and confidence for the withdrawing flag.
ENTROPY_SLOPE_THRESHOLD = float(os.environ.get("ENTROPY_SLOPE_THRESHOLD", "-0.05"))
ENTROPY_WITHDRAW_CONFIDENCE = float(os.environ.get("ENTROPY_WITHDRAW_CONFIDENCE", "0.90"))


def entropy_with_variance(partner_counts: dict[str, int]) -> tuple[float, float]:
    """Shannon entropy (bits) of one week's partner distribution and its sampling variance.

    Variance is the delta-method estimate for the plug-in estimator,
        Var(Ĥ) ≈ (Σ p·log₂²p − H²) / n,
    floored at 1/n bits² because the delta method collapses to 0 for tiny n
    (one event to one partner is "certain" entropy 0, which it is not).  An empty
    week gets variance 1.0 — the same as a single event.

    Calibration note: in isolation this over-states the true sampling variance
    (≈1.5–3× at moderate n), but it was the best of four variants inside the full
    shrinkage pipeline (second-order Basharin term, looser 0.5/n and 0.25/n
    floors): −54 % slope RMSE, 2.1 % false-positive withdrawal flags, 87 % recall
    for well-measured withdrawers, averaged over 3 simulation seeds.  Looser
    variants flagged more people at 7–10 % false positives.
    """
    n = sum(c for c in partner_counts.values() if c > 0)
    if n == 0:
        return 0.0, 1.0
    h = 0.0
    second = 0.0
    for c in partner_counts.values():
        if c <= 0:
            continue
        p = c / n
        lp = math.log2(p)
        h -= p * lp
        second += p * lp * lp
    return h, max((second - h * h) / n, 1.0 / n)


def theil_sen_slope(values: list[float]) -> float:
    """Median of all pairwise slopes (y_j − y_i)/(j − i).  0.0 for fewer than 2 points.

    With 4 weekly points there are 6 pairwise slopes, so a single outlying week
    moves at most half of them — the median is unaffected, unlike an OLS slope.
    """
    n = len(values)
    if n < 2:
        return 0.0
    slopes = [(values[j] - values[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    return float(np.median(slopes))


def slope_sampling_variance(point_variances: list[float]) -> float:
    """Var(slope) for equally spaced points with independent per-point variances.

    OLS formula Σ (x−x̄)² v_k / (Σ (x−x̄)²)².  Used as the variance of the
    Theil-Sen slope too (an approximation: Theil-Sen's efficiency relative to OLS
    is ≈0.9–1 under Gaussian noise).
    """
    n = len(point_variances)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    w = x - x.mean()
    sw = float((w * w).sum())
    return float((w * w * np.asarray(point_variances, dtype=float)).sum() / (sw * sw))


def shrink_toward_center(
    slopes: dict[str, float],
    variances: dict[str, float],
) -> dict[str, tuple[float, float, float]]:
    """Empirical-Bayes (normal–normal) shrinkage of noisy slopes toward the population centre.

        m    = median of the slopes                        (robust centre)
        τ²   = DerSimonian–Laird estimate of the true spread (below)
        B_i  = τ² / (τ² + v_i)                             (share of the estimate kept)
        μ_i  = m + B_i (s_i − m),   sd_i = √(B_i · v_i)    (posterior mean and sd)

    DerSimonian–Laird (the standard random-effects estimator for units with
    heterogeneous *known* variances):
        w_i = 1/v_i,  μ_w = Σ w s / Σ w,  Q = Σ w (s − μ_w)²
        τ²  = max(ε, (Q − (k − 1)) / (Σ w − Σ w² / Σ w))
    The simpler method of moments, Var(s) − mean(v), was tried first and fails
    here: variances span orders of magnitude (2 vs 60 events/week), so a few very
    noisy employees inflate mean(v) and the estimated signal collapses to ~0 —
    on the demo data it shrank everyone onto the median.

    A low-activity employee (large v_i) is pulled most of the way to m; a
    well-measured one keeps most of their own slope.  Returns
    {id: (posterior_mean, posterior_sd, B)}.  With fewer than 3 employees there
    is no population to learn τ² from, so slopes are returned unshrunk.
    """
    ids = list(slopes)
    if len(ids) < 3:
        return {i: (slopes[i], math.sqrt(max(variances.get(i, 0.0), 0.0)), 1.0) for i in ids}
    s = np.array([slopes[i] for i in ids], dtype=float)
    v = np.array([max(variances.get(i, 0.0), 1e-9) for i in ids], dtype=float)
    m = float(np.median(s))
    w = 1.0 / v
    mu_w = float((w * s).sum() / w.sum())
    q = float((w * (s - mu_w) ** 2).sum())
    denom = float(w.sum() - (w * w).sum() / w.sum())
    tau2 = max((q - (len(ids) - 1)) / denom, 1e-6) if denom > 0 else 1e-6
    b = tau2 / (tau2 + v)
    post = m + b * (s - m)
    sd = np.sqrt(b * v)
    return {i: (float(post[k]), float(sd[k]), float(b[k])) for k, i in enumerate(ids)}


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class EntropySignal:
    """Per-employee entropy-trend estimate with its uncertainty.

    ``slope`` is what the rest of the pipeline calls ``entropy_trend``.
    """

    slope: float  # shrunk Theil-Sen slope (bits / week)
    slope_sd: float  # posterior sd
    raw_slope: float  # Theil-Sen slope before shrinkage
    raw_slope_var: float  # its sampling variance
    shrinkage: float  # B ∈ (0, 1]: share of the raw estimate kept
    p_withdrawing: float  # P(true slope < ENTROPY_SLOPE_THRESHOLD)
    withdrawing: bool  # p_withdrawing ≥ ENTROPY_WITHDRAW_CONFIDENCE
    weekly_entropy: tuple[float, ...]
    weekly_events: tuple[int, ...]


def entropy_signals_from_weekly(
    weekly: dict[str, dict[int, dict[str, int]]],
    n_weeks: int = _N_WEEKS,
    threshold: float = ENTROPY_SLOPE_THRESHOLD,
    confidence: float = ENTROPY_WITHDRAW_CONFIDENCE,
) -> dict[str, EntropySignal]:
    """Turn {employee: {week: {partner: count}}} into robust, shrunk entropy signals."""
    raw: dict[str, float] = {}
    var: dict[str, float] = {}
    hist: dict[str, tuple[tuple[float, ...], tuple[int, ...]]] = {}
    for emp_id, week_data in weekly.items():
        hs, vs, ns = [], [], []
        for w in range(n_weeks):
            counts = week_data.get(w, {})
            h, v = entropy_with_variance(counts)
            hs.append(h)
            vs.append(v)
            ns.append(int(sum(c for c in counts.values() if c > 0)))
        raw[emp_id] = theil_sen_slope(hs)
        var[emp_id] = slope_sampling_variance(vs)
        hist[emp_id] = (tuple(round(h, 4) for h in hs), tuple(ns))

    out: dict[str, EntropySignal] = {}
    for emp_id, (mean, sd, b) in shrink_toward_center(raw, var).items():
        p = _normal_cdf((threshold - mean) / sd) if sd > 0 else float(mean < threshold)
        out[emp_id] = EntropySignal(
            slope=mean,
            slope_sd=sd,
            raw_slope=raw[emp_id],
            raw_slope_var=var[emp_id],
            shrinkage=b,
            p_withdrawing=p,
            withdrawing=p >= confidence,
            weekly_entropy=hist[emp_id][0],
            weekly_events=hist[emp_id][1],
        )
    return out


def build_feature_vector(
    current: dict[str, float],
    prev: dict[str, float] | None,
    entropy_current: float,
    entropy_trend: float,
) -> dict[str, float]:
    """Assemble the feature dict for one employee.

    Args:
        current: Metric dict from the current snapshot (betweenness, degree_in,
                 degree_out, clustering).
        prev: Metric dict from 7 days ago (betweenness, degree_out only).
              Pass None or {} when no prior snapshot exists — deltas become 0.
        entropy_current: Shannon entropy of the employee's most recent week.
        entropy_trend: Linear slope of weekly entropy (negative = withdrawing).

    Returns:
        Dict with exactly the keys consumed by IsolationForest.
    """
    p = prev or {}
    cur_b = current.get("betweenness", 0.0)
    cur_d = current.get("degree_out", 0.0)

    return {
        "betweenness": cur_b,
        "degree_in": current.get("degree_in", 0.0),
        "degree_out": cur_d,
        "clustering": current.get("clustering", 0.0),
        "betweenness_delta_7d": cur_b - p.get("betweenness", cur_b),
        "degree_out_delta_7d": cur_d - p.get("degree_out", cur_d),
        "entropy_current": entropy_current,
        "entropy_trend": entropy_trend,
    }


# ─── DB-backed functions ──────────────────────────────────────────────────────


def _load_weekly_interactions(
    window_start: date,
    snapshot_date: date,
    cur,
) -> dict[str, dict[int, dict[str, int]]]:
    """Query raw_events and return per-employee weekly partner counts.

    Returns:
        {employee_id: {week_idx: {partner_id: count}}}
        where week_idx ∈ [0, _N_WEEKS-1] (0 = oldest, _N_WEEKS-1 = newest).
    """
    # Buckets are _N_WEEKS equal 7-day weeks counted BACK from snapshot_date
    # (days 0–6 before it = newest week).  Equal-length buckets matter: a longer
    # final bucket holds more distinct partners and so more entropy, which biased
    # every slope upward.  date − date is an INTEGER day count in Postgres, so
    # integer division gives the week.  (The previous EXTRACT(EPOCH FROM <integer>)
    # raised "function pg_catalog.extract(unknown, integer) does not exist": entropy
    # trends were never computed on real data and silently stayed 0.)
    # Consent/active filters mirror graph.builder.load_raw_edges: an employee who
    # has not consented must not contribute to anyone's entropy either.
    cur.execute(
        """
        SELECT
            re.source_id::text,
            %s - ((%s::date - re.ts::date) / 7) AS week_idx,
            re.target_id::text,
            COUNT(*) AS cnt
        FROM raw_events re
        JOIN employees es ON es.id = re.source_id
        JOIN employees et ON et.id = re.target_id
        WHERE re.ts >= %s::date
          AND (%s::date - re.ts::date) BETWEEN 0 AND %s
          AND es.consent = true AND et.consent = true
          AND es.active  = true AND et.active  = true
        GROUP BY 1, 2, 3
        """,
        (_N_WEEKS - 1, snapshot_date, window_start, snapshot_date, _N_WEEKS * 7 - 1),
    )
    weekly: dict[str, dict[int, dict[str, int]]] = {}
    for emp_id, week_idx, partner_id, cnt in cur.fetchall():
        (weekly.setdefault(emp_id, {}).setdefault(int(week_idx), {}))[partner_id] = int(cnt)
    return weekly


def compute_entropy_signals(
    snapshot_date: date,
    window_days: int = 30,
) -> dict[str, EntropySignal]:
    """Robust, shrunk entropy-trend signal per employee (see entropy_signals_from_weekly)."""
    window_start = snapshot_date - timedelta(days=window_days)
    with get_conn() as conn:
        with conn.cursor() as cur:
            weekly = _load_weekly_interactions(window_start, snapshot_date, cur)
    return entropy_signals_from_weekly(weekly)


def compute_entropy_trends(
    snapshot_date: date,
    window_days: int = 30,
) -> dict[str, float]:
    """Entropy trend per employee: shrunk Theil-Sen slope (negative = withdrawing).

    Used by risk_scorer.task_score_risks for the SPOF δ term.  Callers that also
    need the uncertainty or the withdrawing flag should use compute_entropy_signals.
    """
    return {emp_id: s.slope for emp_id, s in compute_entropy_signals(snapshot_date, window_days).items()}


def extract_features(
    snapshot_date: date,
    window_days: int = 30,
) -> list[dict]:
    """Build a feature vector for every employee in graph_snapshots on snapshot_date.

    Feature keys (8 total):
        betweenness, degree_in, degree_out, clustering,
        betweenness_delta_7d, degree_out_delta_7d,
        entropy_current, entropy_trend

    Plus: employee_id (string, not used as a feature in the model).

    Args:
        snapshot_date: The snapshot to featurise.
        window_days: Rolling window for entropy computation.

    Returns:
        List of feature dicts, one per employee. Empty if no snapshot exists.
    """
    prior_date = snapshot_date - timedelta(days=7)
    window_start = snapshot_date - timedelta(days=window_days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT employee_id::text, betweenness, degree_in, degree_out, clustering
                FROM graph_snapshots
                WHERE snapshot_date = %s
                """,
                (snapshot_date,),
            )
            current_rows = cur.fetchall()

            cur.execute(
                """
                SELECT employee_id::text, betweenness, degree_out
                FROM graph_snapshots
                WHERE snapshot_date = %s
                """,
                (prior_date,),
            )
            prior_rows = cur.fetchall()

            weekly = _load_weekly_interactions(window_start, snapshot_date, cur)

    if not current_rows:
        logger.warning(
            "extract_features: no graph_snapshots for %s — returning empty list",
            snapshot_date,
        )
        return []

    current = {
        r[0]: {
            "betweenness": float(r[1]),
            "degree_in": float(r[2]),
            "degree_out": float(r[3]),
            "clustering": float(r[4]),
        }
        for r in current_rows
    }
    prior = {r[0]: {"betweenness": float(r[1]), "degree_out": float(r[2])} for r in prior_rows}
    signals = entropy_signals_from_weekly(weekly)

    features: list[dict] = []
    for emp_id, metrics in current.items():
        week_data = weekly.get(emp_id, {})
        sig = signals.get(emp_id)

        fv = build_feature_vector(
            current=metrics,
            prev=prior.get(emp_id),
            entropy_current=compute_entropy(week_data.get(_N_WEEKS - 1, {})),
            # Same robust, shrunk trend the SPOF score uses, so the anomaly
            # detector is not fed the noisier raw slope.
            entropy_trend=sig.slope if sig else 0.0,
        )
        fv["employee_id"] = emp_id
        features.append(fv)

    logger.info(
        "extract_features: %d employees, snapshot=%s, prior=%s, window=%dd",
        len(features),
        snapshot_date,
        prior_date,
        window_days,
    )
    return features


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Extract graph features for a snapshot date.")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--window-days",
        type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
    )
    args = parser.parse_args()

    features = extract_features(args.snapshot_date, args.window_days)
    logger.info("Extracted %d feature vectors.", len(features))
    if features:
        trends = [f["entropy_trend"] for f in features]
        withdrawing = sum(1 for t in trends if t < 0)
        logger.info(
            "Entropy trend — min=%.3f max=%.3f withdrawing=%d",
            min(trends),
            max(trends),
            withdrawing,
        )


if __name__ == "__main__":
    main()
