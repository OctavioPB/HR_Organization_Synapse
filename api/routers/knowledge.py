"""Router: /knowledge — knowledge concentration scores and domain expertise."""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db as queries
from api.deps import get_db
from api.models.schemas import (
    DomainExpertise,
    EmployeeKnowledgeProfile,
    KnowledgeDomain,
    KnowledgeDomainsResponse,
    KnowledgeImpactStatement,
    KnowledgeScore,
    KnowledgeScoresResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _resolve_knowledge_date(requested: date | None, conn) -> date:
    if requested is not None:
        return requested
    latest = queries.fetch_latest_knowledge_date(conn)
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="No knowledge scores found. Run the knowledge_score DAG first.",
        )
    return latest


@router.get("/scores", response_model=KnowledgeScoresResponse)
def get_knowledge_scores(
    date: date | None = Query(default=None, description="Scoring date (default: latest)"),
    top: int = Query(default=50, ge=1, le=500),
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    conn=Depends(get_db),
) -> KnowledgeScoresResponse:
    """Top employees by knowledge concentration score.

    Returns employees ordered by knowledge_score descending.
    The knowledge_score reflects how much unique domain knowledge an employee
    holds that cannot be redistributed if they leave.
    """
    computed_at = _resolve_knowledge_date(date, conn)
    rows = queries.fetch_knowledge_scores(computed_at, top, min_score, conn)

    if not rows and date is not None:
        raise HTTPException(
            status_code=404,
            detail=f"No knowledge scores found for date {date}.",
        )

    scores = [
        KnowledgeScore(
            employee_id=r["employee_id"],
            name=r["name"],
            department=r["department"],
            knowledge_score=r["knowledge_score"],
            sole_expert_count=r["sole_expert_count"],
            domain_count=r["domain_count"],
            doc_count=r["doc_count"],
            enhanced_spof_score=r["enhanced_spof_score"],
            impacted_departments=r["impacted_departments"],
            computed_at=r["computed_at"],
        )
        for r in rows
    ]
    return KnowledgeScoresResponse(computed_at=computed_at, total=len(scores), scores=scores)


@router.get("/domains", response_model=KnowledgeDomainsResponse)
def get_knowledge_domains(conn=Depends(get_db)) -> KnowledgeDomainsResponse:
    """All knowledge domains with contributor counts and sole-expert identification.

    Useful for spotting domains where only one person holds expertise.
    """
    rows = queries.fetch_knowledge_domains(conn)
    domains = [
        KnowledgeDomain(
            domain=r["domain"],
            total_docs=r["total_docs"],
            contributor_count=r["contributor_count"],
            sole_expert_id=r["sole_expert_id"],
            sole_expert_name=r["sole_expert_name"],
        )
        for r in rows
    ]
    return KnowledgeDomainsResponse(total=len(domains), domains=domains)


@router.get("/employee/{employee_id}", response_model=EmployeeKnowledgeProfile)
def get_employee_knowledge_profile(
    employee_id: str,
    conn=Depends(get_db),
) -> EmployeeKnowledgeProfile:
    """Full knowledge profile for one employee.

    Includes per-domain breakdown with sole-expert flags and expertise scores.
    """
    profile = queries.fetch_employee_knowledge_profile(employee_id, conn)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=f"No knowledge data for employee {employee_id}.",
        )

    domains = [
        DomainExpertise(
            domain=d["domain"],
            doc_count=d["doc_count"],
            is_sole_expert=d["is_sole_expert"],
            expertise_score=d["expertise_score"],
        )
        for d in profile.get("domains", [])
    ]

    return EmployeeKnowledgeProfile(
        employee_id=profile["employee_id"],
        name=profile["name"],
        department=profile["department"],
        knowledge_score=profile["knowledge_score"],
        sole_expert_count=profile["sole_expert_count"],
        domain_count=profile["domain_count"],
        doc_count=profile["doc_count"],
        enhanced_spof_score=profile["enhanced_spof_score"],
        domains=domains,
        computed_at=profile["computed_at"],
    )


@router.get("/impact/{employee_id}", response_model=KnowledgeImpactStatement)
def get_knowledge_impact(
    employee_id: str,
    conn=Depends(get_db),
) -> KnowledgeImpactStatement:
    """Human-readable impact statement for an employee's departure.

    Answers: "If this person leaves, which departments lose expertise in which domains?"
    """
    from graph.knowledge_risk import get_impact_statement

    result = get_impact_statement(employee_id, conn)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No knowledge risk record for employee {employee_id}.",
        )

    return KnowledgeImpactStatement(
        employee_id=result["employee_id"],
        name=result["name"],
        department=result["department"],
        sole_expert_count=result["sole_expert_count"],
        domain_count=result["domain_count"],
        knowledge_score=result["knowledge_score"],
        enhanced_spof_score=result["enhanced_spof_score"],
        sole_expert_domains=result["sole_expert_domains"],
        impacted_departments=result["impacted_departments"],
        statement=result["statement"],
        computed_at=result["computed_at"],
    )
