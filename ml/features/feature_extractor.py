"""Rolling feature extraction from graph_snapshots and raw_events.

For each employee present in graph_snapshots on snapshot_date, we extract:
  - Current betweenness, degree_in, degree_out, clustering
  - 7-day deltas (vs prior snapshot)
  - Current-week interaction entropy
  - Linear trend of weekly entropy over window_days (negative = withdrawing)

Pure computation functions (no DB, fully testable):
    compute_entropy(partner_counts) → float
    compute_entropy_trend(weekly_entropies) → float
    build_feature_vector(current, prev, entropy_current, entropy_trend) → dict

DB-accessing functions:
    compute_entropy_trends(snapshot_date, window_days) → dict[str, float]
    extract_features(snapshot_date, window_days)       → list[dict]

CLI:
    python ml/features/feature_extractor.py --snapshot-date 2025-04-25
"""

import argparse
import logging
import math
import os
import sys
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
    return -sum(
        (c / total) * math.log2(c / total)
        for c in partner_counts.values()
        if c > 0
    )


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
    cur.execute(
        """
        SELECT
            source_employee_id::text,
            LEAST(
                FLOOR(
                    EXTRACT(EPOCH FROM (ts::date - %s::date)) / 604800
                )::int,
                %s
            ) AS week_idx,
            target_employee_id::text,
            COUNT(*) AS cnt
        FROM raw_events
        WHERE ts >= %s::date
          AND ts < (%s::date + INTERVAL '1 day')
        GROUP BY 1, 2, 3
        """,
        (window_start, _N_WEEKS - 1, window_start, snapshot_date),
    )
    weekly: dict[str, dict[int, dict[str, int]]] = {}
    for emp_id, week_idx, partner_id, cnt in cur.fetchall():
        (
            weekly
            .setdefault(emp_id, {})
            .setdefault(int(week_idx), {})
        )[partner_id] = int(cnt)
    return weekly


def compute_entropy_trends(
    snapshot_date: date,
    window_days: int = 30,
) -> dict[str, float]:
    """Compute linear entropy trend slope per employee over window_days.

    Intended for use by risk_scorer.task_score_risks so that the SPOF
    formula receives real entropy signals instead of zeros.

    Args:
        snapshot_date: End date of the rolling window.
        window_days: Length of the rolling window in days.

    Returns:
        Dict mapping employee_id → slope (negative = withdrawing).
    """
    window_start = snapshot_date - timedelta(days=window_days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            weekly = _load_weekly_interactions(window_start, snapshot_date, cur)

    return {
        emp_id: compute_entropy_trend(
            [compute_entropy(week_data.get(w, {})) for w in range(_N_WEEKS)]
        )
        for emp_id, week_data in weekly.items()
    }


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
        r[0]: {"betweenness": float(r[1]), "degree_in": float(r[2]),
               "degree_out": float(r[3]), "clustering": float(r[4])}
        for r in current_rows
    }
    prior = {
        r[0]: {"betweenness": float(r[1]), "degree_out": float(r[2])}
        for r in prior_rows
    }

    features: list[dict] = []
    for emp_id, metrics in current.items():
        week_data = weekly.get(emp_id, {})
        entropies = [compute_entropy(week_data.get(w, {})) for w in range(_N_WEEKS)]

        fv = build_feature_vector(
            current=metrics,
            prev=prior.get(emp_id),
            entropy_current=entropies[-1],
            entropy_trend=compute_entropy_trend(entropies),
        )
        fv["employee_id"] = emp_id
        features.append(fv)

    logger.info(
        "extract_features: %d employees, snapshot=%s, prior=%s, window=%dd",
        len(features), snapshot_date, prior_date, window_days,
    )
    return features


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Extract graph features for a snapshot date.")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--window-days", type=int,
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
            min(trends), max(trends), withdrawing,
        )


if __name__ == "__main__":
    main()
