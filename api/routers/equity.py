"""Router: /equity — DEI Structural Equity Analytics (Feature 5).

All endpoints return group-level aggregates only.
No individual demographic attributes are exposed in any response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/equity", tags=["equity"])


@router.get("/centrality-distribution")
def get_centrality_distribution(
    dimension: str = Query(default="tenure_band", description="gender_group | tenure_band | level_band"),
    metric:    str = Query(default="betweenness", description="betweenness | degree"),
    conn=Depends(get_db),
) -> dict:
    """Return centrality distribution by demographic group for the latest computed date.

    All values are aggregated at the group level — no individual data exposed.
    """
    valid_dims    = {"gender_group", "tenure_band", "level_band"}
    valid_metrics = {"betweenness", "degree"}
    if dimension not in valid_dims:
        raise HTTPException(status_code=422, detail=f"dimension must be one of: {sorted(valid_dims)}")
    if metric not in valid_metrics:
        raise HTTPException(status_code=422, detail=f"metric must be one of: {sorted(valid_metrics)}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(computed_at) AS latest FROM structural_equity_scores WHERE dimension = %s",
            (dimension,),
        )
        row = cur.fetchone()
        latest = row["latest"] if row else None

    if not latest:
        raise HTTPException(
            status_code=404,
            detail="No equity scores found. Run the equity_dag first, and ensure demographic data is imported.",
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT group_value, metric, median_score, p25_score, p75_score,
                   member_count, below_org_median
            FROM structural_equity_scores
            WHERE dimension = %s AND metric = %s AND computed_at = %s
            ORDER BY median_score DESC NULLS LAST
            """,
            (dimension, metric, latest),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "computed_at": str(latest),
        "dimension":   dimension,
        "metric":      metric,
        "groups": [
            {
                "group_value":      r["group_value"],
                "median_score":     round(float(r["median_score"]), 6) if r["median_score"] is not None else None,
                "p25_score":        round(float(r["p25_score"]),    6) if r["p25_score"]    is not None else None,
                "p75_score":        round(float(r["p75_score"]),    6) if r["p75_score"]    is not None else None,
                "member_count":     int(r["member_count"] or 0),
                "below_org_median": bool(r["below_org_median"]),
            }
            for r in rows
        ],
    }


@router.get("/succession-check/{spof_employee_id}")
def get_succession_equity_check(
    spof_employee_id: str,
    conn=Depends(get_db),
) -> dict:
    """Check the demographic composition of succession candidates for one SPOF employee.

    Returns group-level composition statistics and a homophily warning flag.
    No individual demographic attributes are returned — only group counts.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sr.candidate_employee_id::text, d.tenure_band, d.level_band
            FROM succession_recommendations sr
            LEFT JOIN employee_demographics d ON d.employee_id = sr.candidate_employee_id
            WHERE sr.source_employee_id = %s::uuid
              AND sr.computed_at = (SELECT MAX(computed_at) FROM succession_recommendations)
            ORDER BY sr.rank
            """,
            (spof_employee_id,),
        )
        candidates = [dict(r) for r in cur.fetchall()]

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No succession candidates found for employee {spof_employee_id}.",
        )

    # Compute tenure_band composition
    tenure_counts: dict[str, int] = {}
    level_counts:  dict[str, int] = {}
    for c in candidates:
        tb = c.get("tenure_band") or "unknown"
        lb = c.get("level_band")  or "unknown"
        tenure_counts[tb] = tenure_counts.get(tb, 0) + 1
        level_counts[lb]  = level_counts.get(lb, 0) + 1

    total = len(candidates)
    max_tenure_pct = max(tenure_counts.values()) / total if tenure_counts else 0.0
    max_level_pct  = max(level_counts.values())  / total if level_counts  else 0.0
    homophily_warning = (max_tenure_pct > 0.7 or max_level_pct > 0.7)

    return {
        "spof_employee_id":    spof_employee_id,
        "total_candidates":    total,
        "tenure_band_composition": {k: round(v / total, 3) for k, v in tenure_counts.items()},
        "level_band_composition":  {k: round(v / total, 3) for k, v in level_counts.items()},
        "homophily_warning":   homophily_warning,
        "dominant_group_pct":  round(max(max_tenure_pct, max_level_pct), 3),
    }


@router.post("/import-demographics")
def import_demographics(
    records: list[dict],
    conn=Depends(get_db),
) -> dict:
    """Import demographic group labels for employees.

    Accepts a list of: {employee_id, gender_group, tenure_band, level_band}.
    All group labels are anonymised — use descriptive labels like 'group_a' or '1-3y'.
    Only employees with consent=TRUE in the employees table are accepted.
    """
    valid_tenures = {"0-1y", "1-3y", "3-5y", "5y+"}
    valid_levels  = {"ic", "senior_ic", "manager", "director_plus"}

    imported = 0
    skipped  = 0

    with conn.cursor() as cur:
        for rec in records:
            emp_id = rec.get("employee_id", "")
            if not emp_id:
                skipped += 1
                continue

            # Validate employee exists and consents
            cur.execute(
                "SELECT consent FROM employees WHERE id = %s::uuid AND active = TRUE",
                (emp_id,),
            )
            emp = cur.fetchone()
            if not emp or not emp["consent"]:
                skipped += 1
                continue

            tenure = rec.get("tenure_band")
            level  = rec.get("level_band")
            if tenure and tenure not in valid_tenures:
                skipped += 1
                continue
            if level and level not in valid_levels:
                skipped += 1
                continue

            cur.execute(
                """
                INSERT INTO employee_demographics (employee_id, gender_group, tenure_band, level_band, consent, source)
                VALUES (%s::uuid, %s, %s, %s, TRUE, 'manual')
                ON CONFLICT (employee_id) DO UPDATE SET
                  gender_group = COALESCE(EXCLUDED.gender_group, employee_demographics.gender_group),
                  tenure_band  = COALESCE(EXCLUDED.tenure_band,  employee_demographics.tenure_band),
                  level_band   = COALESCE(EXCLUDED.level_band,   employee_demographics.level_band),
                  source = 'manual'
                """,
                (emp_id, rec.get("gender_group"), tenure, level),
            )
            imported += 1

    conn.commit()
    return {"imported": imported, "skipped": skipped}
