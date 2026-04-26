"""Router: /succession — cross-training recommendations for high-SPOF employees."""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

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
