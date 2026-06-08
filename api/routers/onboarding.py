"""Router: /onboarding — new hire graph integration tracker."""

import logging
from datetime import date

from fastapi import APIRouter, Depends, Query

from api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/cohort")
def get_onboarding_cohort(
    scored_date: date | None = Query(default=None),
    conn=Depends(get_db),
) -> dict:
    """Return all new hires (last 180 days) with their latest integration scores."""
    with conn.cursor() as cur:
        if scored_date is None:
            cur.execute("SELECT MAX(scored_date) AS d FROM onboarding_integration_scores")
            row = cur.fetchone()
            scored_date = row["d"] if row and row["d"] else date.today()

        cur.execute(
            """
            SELECT
                ois.employee_id::text,
                e.name,
                e.department,
                e.hire_date,
                (%s::date - e.hire_date) AS tenure_days,
                ois.integration_score,
                ois.degree_centrality_pct,
                ois.cross_dept_edge_count,
                ois.community_stability,
                ois.cohort_size,
                ois.below_cohort_threshold,
                ois.scored_date
            FROM onboarding_integration_scores ois
            JOIN employees e ON e.id = ois.employee_id
            WHERE ois.scored_date = %s
              AND e.active = TRUE
            ORDER BY ois.integration_score ASC
            """,
            (scored_date, scored_date),
        )
        rows = [dict(r) for r in cur.fetchall()]

    scores = [float(r["integration_score"]) for r in rows]
    cohort_median = sorted(scores)[len(scores) // 2] if scores else 0.0
    at_risk_count = sum(1 for r in rows if r["below_cohort_threshold"])

    return {
        "scored_date": str(scored_date),
        "cohort_size": len(rows),
        "cohort_median_score": round(cohort_median, 4),
        "at_risk_count": at_risk_count,
        "cohort": [
            {
                "employee_id": r["employee_id"],
                "name": r["name"],
                "department": r["department"],
                "hire_date": str(r["hire_date"]) if r["hire_date"] else None,
                "tenure_days": int(r["tenure_days"] or 0),
                "integration_score": round(float(r["integration_score"]), 4),
                "degree_centrality_pct": round(float(r["degree_centrality_pct"] or 0), 4),
                "cross_dept_edge_count": int(r["cross_dept_edge_count"] or 0),
                "community_stability": round(float(r["community_stability"] or 0), 4),
                "cohort_size": int(r["cohort_size"] or 0),
                "below_cohort_threshold": bool(r["below_cohort_threshold"]),
            }
            for r in rows
        ],
    }


@router.get("/employee/{employee_id}/history")
def get_onboarding_history(
    employee_id: str,
    conn=Depends(get_db),
) -> dict:
    """Return time-series integration scores for a new hire (up to 180 rows)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ois.scored_date,
                ois.integration_score,
                ois.degree_centrality_pct,
                ois.cross_dept_edge_count,
                ois.community_stability,
                ois.below_cohort_threshold
            FROM onboarding_integration_scores ois
            WHERE ois.employee_id = %s::uuid
            ORDER BY ois.scored_date ASC
            LIMIT 180
            """,
            (employee_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "employee_id": employee_id,
        "history": [
            {
                "scored_date": str(r["scored_date"]),
                "integration_score": round(float(r["integration_score"]), 4),
                "degree_centrality_pct": round(float(r["degree_centrality_pct"] or 0), 4),
                "cross_dept_edge_count": int(r["cross_dept_edge_count"] or 0),
                "below_cohort_threshold": bool(r["below_cohort_threshold"]),
            }
            for r in rows
        ],
    }
