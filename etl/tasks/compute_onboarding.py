"""Compute onboarding integration scores for new hires (hire_date within last 180 days).

For each new hire, compares their graph position against the cohort median
for employees of similar tenure (30-day bands). Fires an alert when a new hire
is below the 25th percentile at day 60+.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_COHORT_ALERT_DAY = int(os.environ.get("ONBOARDING_ALERT_DAY", "60"))
_ONBOARDING_WINDOW = int(os.environ.get("ONBOARDING_WINDOW_DAYS", "180"))


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

    upserted = 0
    alerts_fired = 0

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

            # Community stability (same community as 7 days ago)
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
            community_stability = (
                1.0 if (prev and curr and prev["community_id"] == curr["community_id"])
                else 0.0
            )

            # Integration score: 50% degree percentile, 30% cross-dept, 20% community stability
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
                 round(degree_pct_norm, 4), cross_dept, community_stability,
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
    logger.info("Onboarding: %d scores upserted, %d alerts fired.", upserted, alerts_fired)
    return {"processed": upserted, "alerts_fired": alerts_fired, "snapshot_date": snapshot_date_str}
