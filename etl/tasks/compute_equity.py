"""Compute structural equity scores by demographic dimension.

Groups employees by dimension (gender_group, tenure_band, level_band) and
computes percentile distributions of centrality metrics. All computation is
at the aggregate level — no individual demographic attributes leave this module.
"""

from __future__ import annotations

import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

_DIMENSIONS = ["gender_group", "tenure_band", "level_band"]
_METRICS = ["betweenness", "degree", "cross_dept_ratio"]

# Disparity ratio that triggers a `below_org_median` flag (MODEL.md §11.1).
#
# This 0.8 ratio is adapted *by analogy* from the EEOC four-fifths rule
# (29 C.F.R. § 1607.4D), which applies to employment *selection rates*.
# Betweenness/degree centrality are NOT selection rates, so here the threshold
# is an INVESTIGATIVE HEURISTIC, not a legal standard: a flag means a group's
# median structural position is far enough below the org median to warrant
# qualitative human investigation of whether structural barriers exist. It is
# not evidence of discrimination or legal liability. Configurable so it is never
# mistaken for a fixed legal line.
_DISPARITY_RATIO = float(os.environ.get("EQUITY_DISPARITY_RATIO", "0.8"))


def task_compute_equity(snapshot_date_str: str, conn) -> dict:
    snapshot_date = date.fromisoformat(snapshot_date_str)
    logger.info("Computing structural equity scores for %s …", snapshot_date)

    # Check that demographic data exists
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM employee_demographics WHERE consent = TRUE")
        consent_count = cur.fetchone()["cnt"]

    if consent_count == 0:
        logger.info("No consenting demographic records — skipping equity computation.")
        return {"skipped": True, "reason": "no_demographic_data"}

    rows_written = 0

    for dimension in _DIMENSIONS:
        # Build per-employee metric values joined with demographics
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH employee_metrics AS (
                    SELECT
                        gs.employee_id,
                        COALESCE(gs.betweenness, 0)     AS betweenness,
                        COALESCE(gs.degree_in, 0) + COALESCE(gs.degree_out, 0) AS degree,
                        -- cross_dept_ratio approximated from graph snapshot
                        COALESCE(gs.clustering, 0)       AS clustering
                    FROM graph_snapshots gs
                    WHERE gs.snapshot_date = %s
                ),
                demo_metrics AS (
                    SELECT
                        d.{dimension} AS group_val,
                        em.betweenness,
                        em.degree,
                        em.clustering
                    FROM employee_demographics d
                    JOIN employee_metrics em ON em.employee_id = d.employee_id
                    WHERE d.consent = TRUE
                      AND d.{dimension} IS NOT NULL
                )
                SELECT
                    group_val,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY betweenness) AS p25_bw,
                    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY betweenness) AS med_bw,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY betweenness) AS p75_bw,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY degree) AS p25_deg,
                    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY degree) AS med_deg,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY degree) AS p75_deg,
                    COUNT(*) AS member_count
                FROM demo_metrics
                WHERE group_val IS NOT NULL
                GROUP BY group_val
                """,
                (snapshot_date,),
            )
            group_rows = [dict(r) for r in cur.fetchall()]

        if not group_rows:
            continue

        # Compute global medians
        global_med_bw = _median([r["med_bw"] for r in group_rows if r["med_bw"] is not None])
        global_med_deg = _median([r["med_deg"] for r in group_rows if r["med_deg"] is not None])

        with conn.cursor() as cur:
            for row in group_rows:
                group_val = row["group_val"]
                count = int(row["member_count"] or 0)

                for metric, median_v, p25_v, p75_v, global_med in [
                    ("betweenness", row["med_bw"], row["p25_bw"], row["p75_bw"], global_med_bw),
                    ("degree", row["med_deg"], row["p25_deg"], row["p75_deg"], global_med_deg),
                ]:
                    below = median_v is not None and global_med is not None and median_v < global_med * _DISPARITY_RATIO
                    cur.execute(
                        """
                        INSERT INTO structural_equity_scores
                          (dimension, group_value, metric, median_score, p25_score, p75_score,
                           member_count, below_org_median)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            dimension,
                            group_val,
                            metric,
                            float(median_v) if median_v is not None else None,
                            float(p25_v) if p25_v is not None else None,
                            float(p75_v) if p75_v is not None else None,
                            count,
                            below,
                        ),
                    )
                    rows_written += 1

    conn.commit()
    logger.info("Structural equity: %d score rows written.", rows_written)
    return {"rows_written": rows_written, "snapshot_date": snapshot_date_str}


def _median(values: list) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    s = sorted(clean)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2 if n % 2 == 0 else s[n // 2]
