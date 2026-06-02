"""Router: /succession — cross-training recommendations for high-SPOF employees."""

import logging
from datetime import date

import io
import json as _json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api import db as queries
from api.deps import get_db
from api.models.schemas import (
    SuccessionCandidate,
    SuccessionRecommendation,
    SuccessionResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/succession", tags=["succession"])


def _resolve_succession_date(requested: date | None, conn) -> date:
    if requested is not None:
        return requested
    latest = queries.fetch_latest_succession_date(conn)
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="No succession recommendations found. Run the succession_dag first.",
        )
    return latest


def _build_recommendation(row: dict) -> SuccessionRecommendation:
    candidates = [
        SuccessionCandidate(
            candidate_employee_id=c["candidate_employee_id"],
            name=c["candidate_name"],
            department=c["candidate_department"],
            compatibility_score=c["compatibility_score"],
            structural_overlap=c["structural_overlap"] or 0.0,
            clustering_score=c["clustering_score"] or 0.0,
            domain_overlap=c["domain_overlap"] or 0.0,
            rank=c["rank"],
        )
        for c in row.get("candidates", [])
    ]
    return SuccessionRecommendation(
        source_employee_id=row["source_employee_id"],
        source_name=row["source_name"],
        source_department=row["source_department"],
        spof_score=row.get("spof_score") or 0.0,
        computed_at=row["computed_at"],
        candidates=candidates,
    )


@router.get("/recommendations", response_model=SuccessionResponse)
def get_succession_recommendations(
    date: date | None = Query(default=None, description="Computation date (default: latest)"),
    top_spof: int = Query(default=20, ge=1, le=200, description="Max source employees to return"),
    min_spof_score: float = Query(default=0.0, ge=0.0, le=1.0),
    conn=Depends(get_db),
) -> SuccessionResponse:
    """Cross-training recommendations for all high-SPOF employees.

    Returns a list of SPOF employees, each with their top succession candidates
    ranked by compatibility score. Compatibility is a weighted combination of:
    - structural_overlap: Jaccard similarity of collaboration networks
    - clustering_score: how embedded the candidate is in their own community
    - domain_overlap: fraction of SPOF's knowledge domains the candidate covers
    """
    computed_at = _resolve_succession_date(date, conn)
    rows = queries.fetch_succession_recommendations(
        computed_at, top_spof, min_spof_score, conn
    )

    if not rows and date is not None:
        raise HTTPException(
            status_code=404,
            detail=f"No succession recommendations found for date {date}.",
        )

    recommendations = [_build_recommendation(r) for r in rows]
    return SuccessionResponse(
        computed_at=computed_at,
        total=len(recommendations),
        recommendations=recommendations,
    )


@router.get("/employee/{employee_id}", response_model=SuccessionRecommendation)
def get_employee_succession(
    employee_id: str,
    conn=Depends(get_db),
) -> SuccessionRecommendation:
    """Succession plan for one specific SPOF employee.

    Returns the most recent cross-training candidates ranked by compatibility.
    Useful for drilling into a specific employee's succession strategy.
    """
    data = queries.fetch_employee_succession(employee_id, conn)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No succession plan for employee {employee_id}. "
                "Either this employee is not flagged as high-SPOF or "
                "the succession_dag has not run yet."
            ),
        )
    return _build_recommendation(data)


# ─── Knowledge Transfer Plan endpoints (Feature 6) ───────────────────────────


@router.get("/{employee_id}/transfer-plan", summary="Knowledge transfer plan for a SPOF employee")
def get_transfer_plan(employee_id: str, conn=Depends(get_db)) -> dict:
    """Return the generated 90-day transfer plan targeting top succession candidates."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ktp.id::text,
                ktp.spof_employee_id::text,
                ktp.candidate_id::text,
                ec.name AS candidate_name,
                ec.department AS candidate_dept,
                ktp.plan_json,
                ktp.generated_at,
                ktp.status
            FROM knowledge_transfer_plans ktp
            JOIN employees ec ON ec.id = ktp.candidate_id
            WHERE ktp.spof_employee_id = %s::uuid
              AND ktp.status = 'active'
            ORDER BY ktp.generated_at DESC
            LIMIT 2
            """,
            (employee_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No transfer plan for employee {employee_id}. Run the succession_dag first.",
        )

    return {
        "spof_employee_id": employee_id,
        "plans": [
            {
                "plan_id":        r["id"],
                "candidate_id":   r["candidate_id"],
                "candidate_name": r["candidate_name"],
                "candidate_dept": r["candidate_dept"],
                "generated_at":   str(r["generated_at"]),
                "status":         r["status"],
                "plan_json":      r["plan_json"],
            }
            for r in rows
        ],
    }


@router.get("/{employee_id}/transfer-plan/export.csv", summary="Export transfer plan as CSV")
def export_transfer_plan_csv(employee_id: str, conn=Depends(get_db)) -> StreamingResponse:
    """Export all transfer plan actions as a downloadable CSV."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plan_json, candidate_id::text
            FROM knowledge_transfer_plans
            WHERE spof_employee_id = %s::uuid AND status = 'active'
            ORDER BY generated_at DESC
            LIMIT 2
            """,
            (employee_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail="No transfer plan found.")

    lines = ["week_range,action_type,description,candidate_id"]
    for row in rows:
        plan = row["plan_json"] or {}
        cand_id = row["candidate_id"]
        for action in plan.get("weeks_1_4", []):
            desc = action.get("description", "").replace('"', '""')
            lines.append(f'"Weeks 1-4","introduction","{desc}","{cand_id}"')
        for action in plan.get("weeks_5_8", []):
            desc = action.get("description", "").replace('"', '""')
            lines.append(f'"Weeks 5-8","document_review","{desc}","{cand_id}"')
        for action in plan.get("weeks_9_12", []):
            desc = action.get("description", "").replace('"', '""')
            lines.append(f'"Weeks 9-12","shadow","{desc}","{cand_id}"')

    csv_content = "\n".join(lines)
    return StreamingResponse(
        io.BytesIO(csv_content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=transfer_plan_{employee_id}.csv"},
    )
