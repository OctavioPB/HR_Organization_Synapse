"""Compute structural equity scores by demographic dimension.

Groups employees by dimension (gender_group, tenure_band, level_band) and tests
whether each group's centrality is lower than everyone else's (permutation
test on the ratio of means, Benjamini–Hochberg across the run; graph/equity_stats.py).
Individual values are used in memory only; only aggregates of groups with at
least MIN_GROUP_SIZE members are written.
"""

from __future__ import annotations

import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

_DIMENSIONS = ["gender_group", "tenure_band", "level_band"]
_METRICS = ["betweenness", "degree"]  # cross_dept_ratio was listed but never computed

# Disparity ratio: the size-of-gap half of a `below_org_median` flag (MODEL.md §11.1).
# The other half is a permutation test with FDR control (graph/equity_stats.py).
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

# k-anonymity floor for group aggregates.  A median over 1–4 people can reveal
# an individual's structural position, so groups smaller than this are never
# persisted (enforced in graph.equity_stats.group_tests) and never returned by /equity/* (enforced
# again in the router, which also covers rows written before this guard existed).
# Clamped to >= 2: a "group" of one is an individual record by definition.
MIN_GROUP_SIZE = max(2, int(os.environ.get("EQUITY_MIN_GROUP_SIZE", "5")))


def task_compute_equity(snapshot_date_str: str, conn) -> dict:
    """Permutation-tested, FDR-controlled structural-equity scores (graph/equity_stats.py)."""
    from graph.equity_stats import apply_fdr, group_tests

    snapshot_date = date.fromisoformat(snapshot_date_str)
    logger.info("Computing structural equity scores for %s …", snapshot_date)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM employee_demographics WHERE consent = TRUE")
        consent_count = cur.fetchone()["cnt"]

    if consent_count == 0:
        logger.info("No consenting demographic records — skipping equity computation.")
        return {"skipped": True, "reason": "no_demographic_data"}

    # Individual rows stay in this process; only k-anonymous aggregates are written.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.gender_group, d.tenure_band, d.level_band,
                   COALESCE(gs.betweenness, 0)                           AS betweenness,
                   COALESCE(gs.degree_in, 0) + COALESCE(gs.degree_out, 0) AS degree
            FROM employee_demographics d
            JOIN graph_snapshots gs ON gs.employee_id = d.employee_id
            WHERE d.consent = TRUE AND gs.snapshot_date = %s
            """,
            (snapshot_date,),
        )
        people = [dict(r) for r in cur.fetchall()]

    tests = []
    for dimension in _DIMENSIONS:
        rows = [r for r in people if r.get(dimension) is not None]
        if not rows:
            continue
        labels = [r[dimension] for r in rows]
        for metric in _METRICS:
            tests += group_tests(
                [float(r[metric]) for r in rows], labels, dimension, metric, min_group_size=MIN_GROUP_SIZE
            )
    tests = apply_fdr(tests, disparity_ratio=_DISPARITY_RATIO)

    with conn.cursor() as cur:
        for t in tests:
            cur.execute(
                """
                INSERT INTO structural_equity_scores
                  (dimension, group_value, metric, median_score, p25_score, p75_score,
                   member_count, below_org_median, mean_ratio, effect_size, p_value, q_value)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    t.dimension,
                    t.group,
                    t.metric,
                    t.median,
                    t.p25,
                    t.p75,
                    t.n,
                    t.flagged,
                    t.mean_ratio,
                    t.effect_size,
                    t.p_value,
                    t.q_value,
                ),
            )

    conn.commit()
    flagged = sum(t.flagged for t in tests)
    logger.info("Structural equity: %d tests written, %d flagged (FDR-controlled).", len(tests), flagged)
    return {"rows_written": len(tests), "flagged": flagged, "snapshot_date": snapshot_date_str}
