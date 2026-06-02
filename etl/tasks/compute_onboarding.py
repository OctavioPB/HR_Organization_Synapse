"""Compute onboarding integration scores for new hires (hire_date within last 180 days).

For each new hire, compares their graph position against the cohort median
for employees of similar tenure (30-day bands). Fires an alert when a new hire
is below the 25th percentile at day 60+.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_COHORT_ALERT_DAY = int(os.environ.get("ONBOARDING_ALERT_DAY", "60"))
_ONBOARDING_WINDOW = int(os.environ.get("ONBOARDING_WINDOW_DAYS", "180"))
# Minimum CS_overlap (Jaccard of community membership across weeks) for an
# onboarding cohort to be considered structurally stable (MODEL.md §9.1).
_CS_OVERLAP_THRESHOLD = float(os.environ.get("CS_OVERLAP_THRESHOLD", "0.6"))


def _load_community_membership(snapshot_date: date, conn) -> dict[int, set[str]]:
    """Return community_id → set of employee_ids in that community at *snapshot_date*.

    Used to compute neighborhood community overlap.  Community *IDs* are not
    comparable across runs (Louvain is non-deterministic and labels are
    arbitrary), so we compare the *membership sets* themselves.
    """
    members: dict[int, set[str]] = defaultdict(set)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT employee_id::text, community_id
            FROM graph_snapshots
            WHERE snapshot_date = %s AND community_id IS NOT NULL
            """,
            (snapshot_date,),
        )
        for emp_id, cid in cur.fetchall():
            members[int(cid)].add(str(emp_id))
    return dict(members)


def compute_cs_overlap(
    emp_id: str,
    cid_curr: int | None,
    cid_prev: int | None,
    curr_members: dict[int, set[str]],
    prev_members: dict[int, set[str]],
) -> float:
    """Neighborhood Community Overlap CS_overlap(v, t) (MODEL.md §9.1).

    Jaccard similarity between the set of people in v's community this week and
    the set in v's community last week:

        CS_overlap(v, t) = |Com(v, t) ∩ Com(v, t−7d)| / |Com(v, t) ∪ Com(v, t−7d)|

    This measures whether the *people* around v are stable, rather than whether
    an arbitrary integer community label happens to match across runs.

    Returns 0.0 when either week's community membership is unavailable.
    """
    if cid_curr is None or cid_prev is None:
        return 0.0
    com_curr = curr_members.get(int(cid_curr), set())
    com_prev = prev_members.get(int(cid_prev), set())
    union = com_curr | com_prev
    if not union:
        return 0.0
    return len(com_curr & com_prev) / len(union)


def task_compute_onboarding(snapshot_date_str: str, conn) -> dict:
    """Compute and persist onboarding integration scores for the given date."""
    snapshot_date = date.fromisoformat(snapshot_date_str)
    logger.info("Computing onboarding scores for %s …", snapshot_date)

    cutoff = snapshot_date - timedelta(days=_ONBOARDING_WINDOW)

    with conn.cursor() as cur:
        # New hires with their graph metrics for today
        cur.execute(
            """
            WITH new_hires AS (
                SELECT
                    e.id::text                                  AS employee_id,
                    e.name,
                    e.department,
                    e.hire_date,
                    (%s::date - e.hire_date) AS tenure_days,
                    -- Group into 30-day bands for cohort comparison
                    ((%s::date - e.hire_date) / 30) AS tenure_band,
                    COALESCE(gs.degree_in, 0) + COALESCE(gs.degree_out, 0) AS degree_total,
                    COALESCE(gs.clustering, 0)                  AS clustering
                FROM employees e
                LEFT JOIN graph_snapshots gs
                    ON gs.employee_id = e.id AND gs.snapshot_date = %s
                WHERE e.hire_date >= %s
                  AND e.hire_date <= %s
                  AND e.active = TRUE
                  AND e.consent = TRUE
            ),
            cohort_stats AS (
                SELECT
                    tenure_band,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY degree_total) AS p25_degree,
                    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY degree_total) AS median_degree,
                    COUNT(*) AS cohort_size
                FROM new_hires
                GROUP BY tenure_band
            )
            SELECT
                nh.*,
                cs.p25_degree,
                cs.median_degree,
                cs.cohort_size,
                CASE WHEN cs.median_degree > 0
                    THEN nh.degree_total::float / cs.median_degree
                    ELSE 0.0
                END AS degree_pct_of_median
            FROM new_hires nh
            JOIN cohort_stats cs ON cs.tenure_band = nh.tenure_band
            """,
            (snapshot_date, snapshot_date, snapshot_date, cutoff, snapshot_date),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        logger.info("No new hires found in the last %d days.", _ONBOARDING_WINDOW)
        return {"processed": 0}

    # Load community membership sets for this week and last week once, so
    # CS_overlap is computed against stable membership rather than arbitrary IDs.
    prev_date = snapshot_date - timedelta(days=7)
    curr_members = _load_community_membership(snapshot_date, conn)
    prev_members = _load_community_membership(prev_date, conn)

    upserted = 0
    alerts_fired = 0
    stable_count = 0  # cohorts with CS_overlap ≥ threshold (MODEL.md §9.1)

    with conn.cursor() as cur:
        for row in rows:
            emp_id       = row["employee_id"]
            tenure_days  = int(row["tenure_days"] or 0)
            degree_total = float(row["degree_total"] or 0)
            median_degree = float(row["median_degree"] or 1)
            p25_degree   = float(row["p25_degree"] or 0)
            cohort_size  = int(row["cohort_size"] or 0)
            clustering   = float(row["clustering"] or 0)
            dept         = row["department"]

            # Cross-dept edge count in last 30 days
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM raw_events re
                JOIN employees es ON es.id = re.source_id
                JOIN employees et ON et.id = re.target_id
                WHERE re.source_id = %s::uuid
                  AND re.ts >= %s::date - INTERVAL '30 days'
                  AND et.department != es.department
                """,
                (emp_id, snapshot_date),
            )
            cross_dept = int(cur.fetchone()["cnt"] or 0)

            # Neighborhood Community Overlap (CS_overlap) — Jaccard of community
            # membership this week vs last week (MODEL.md §9.1).
            cur.execute(
                """
                SELECT community_id FROM graph_snapshots
                WHERE employee_id = %s::uuid AND snapshot_date = %s::date - INTERVAL '7 days'
                LIMIT 1
                """,
                (emp_id, snapshot_date),
            )
            prev = cur.fetchone()
            cur.execute(
                "SELECT community_id FROM graph_snapshots WHERE employee_id = %s::uuid AND snapshot_date = %s LIMIT 1",
                (emp_id, snapshot_date),
            )
            curr = cur.fetchone()
            cid_prev = prev["community_id"] if prev else None
            cid_curr = curr["community_id"] if curr else None
            community_stability = compute_cs_overlap(
                emp_id, cid_curr, cid_prev, curr_members, prev_members
            )
            if community_stability >= _CS_OVERLAP_THRESHOLD:
                stable_count += 1

            # Integration score: 50% degree percentile, 30% cross-dept, 20% CS_overlap
            degree_pct_norm = min(degree_total / max(median_degree, 1), 1.0)
            cross_dept_norm = min(cross_dept / 5.0, 1.0)
            integration_score = (
                0.5 * degree_pct_norm +
                0.3 * cross_dept_norm +
                0.2 * community_stability
            )

            below_threshold = (degree_total <= p25_degree and tenure_days >= _COHORT_ALERT_DAY)

            # Upsert onboarding score
            cur.execute(
                """
                INSERT INTO onboarding_integration_scores
                  (employee_id, scored_date, integration_score, degree_centrality_pct,
                   cross_dept_edge_count, community_stability, cohort_size, below_cohort_threshold)
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (employee_id, scored_date) DO UPDATE SET
                  integration_score      = EXCLUDED.integration_score,
                  degree_centrality_pct  = EXCLUDED.degree_centrality_pct,
                  cross_dept_edge_count  = EXCLUDED.cross_dept_edge_count,
                  community_stability    = EXCLUDED.community_stability,
                  cohort_size            = EXCLUDED.cohort_size,
                  below_cohort_threshold = EXCLUDED.below_cohort_threshold
                """,
                (emp_id, snapshot_date, round(integration_score, 4),
                 round(degree_pct_norm, 4), cross_dept, round(community_stability, 4),
                 cohort_size, below_threshold),
            )
            upserted += 1

            # Fire alert for at-risk new hires
            if below_threshold:
                cur.execute(
                    """
                    INSERT INTO alerts (type, severity, affected_entities, details)
                    VALUES ('onboarding_risk', 'medium',
                            %s::jsonb,
                            %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        __import__("json").dumps({
                            "employee_id": emp_id,
                            "department": dept,
                            "tenure_days": tenure_days,
                            "integration_score": round(integration_score, 4),
                        }),
                        f"New hire in {dept} is below cohort threshold after {tenure_days} days "
                        f"(score={round(integration_score, 2)})",
                    ),
                )
                alerts_fired += 1

    conn.commit()
    logger.info(
        "Onboarding: %d scores upserted, %d alerts fired, %d cohorts structurally stable (CS_overlap ≥ %.2f).",
        upserted, alerts_fired, stable_count, _CS_OVERLAP_THRESHOLD,
    )
    return {
        "processed": upserted,
        "alerts_fired": alerts_fired,
        "stable_count": stable_count,
        "snapshot_date": snapshot_date_str,
    }
