"""Router: /org-health — Org Health Score and Executive Briefing (F9)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db as queries
from api.deps import get_db
from api.models.schemas import (
    OrgHealthBriefing,
    OrgHealthComponentScores,
    OrgHealthScore,
    OrgHealthTrend,
    OrgHealthTrendPoint,
    RiskFactor,
)

router = APIRouter(prefix="/org-health", tags=["org-health"])
logger = logging.getLogger(__name__)


def _resolve_health(conn) -> dict:
    """Return the latest health row or raise 404."""
    row = queries.fetch_latest_org_health(conn)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No org health scores found. Run the org_health_dag first.",
        )
    return row


def _parse_component_scores(raw) -> OrgHealthComponentScores:
    """Coerce a JSONB dict or JSON string into OrgHealthComponentScores."""
    import json

    if isinstance(raw, str):
        raw = json.loads(raw)
    raw = raw or {}
    return OrgHealthComponentScores(
        silo=float(raw.get("silo", 0.0)),
        spof=float(raw.get("spof", 0.0)),
        entropy=float(raw.get("entropy", 0.0)),
        frag=float(raw.get("frag", 0.0)),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/score", response_model=OrgHealthScore)
def get_org_health_score(conn=Depends(get_db)) -> OrgHealthScore:
    """Current composite Org Health Score (0–100) with component breakdown.

    Score is computed by the weekly org_health_dag.  Returns 404 if the DAG
    has not run yet.
    """
    row = _resolve_health(conn)
    return OrgHealthScore(
        computed_at=row["computed_at"],
        score=row["score"],
        tier=row["tier"],
        silo_count=row["silo_count"],
        avg_spof_score=row["avg_spof_score"],
        avg_entropy_trend=row.get("avg_entropy_trend"),
        wcc_count=row["wcc_count"],
        node_count=row["node_count"],
        component_scores=_parse_component_scores(row.get("component_scores")),
    )


@router.get("/trend", response_model=OrgHealthTrend)
def get_org_health_trend(
    weeks: int = Query(default=8, ge=1, le=52),
    conn=Depends(get_db),
) -> OrgHealthTrend:
    """Weekly Org Health Score trend for the last N weeks (oldest-first).

    Useful for sparklines and trend analysis in executive dashboards.
    Returns 404 if no history exists.
    """
    rows = queries.fetch_org_health_trend(weeks, conn)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No org health history found. Run the org_health_dag first.",
        )
    points = [
        OrgHealthTrendPoint(
            computed_at=r["computed_at"],
            score=r["score"],
            tier=r["tier"],
            silo_count=r["silo_count"],
            avg_spof_score=r["avg_spof_score"],
        )
        for r in rows
    ]
    return OrgHealthTrend(weeks=len(points), points=points)


@router.get("/briefing", response_model=OrgHealthBriefing)
async def get_org_health_briefing(
    trend_weeks: int = Query(default=4, ge=2, le=12),
    conn=Depends(get_db),
) -> OrgHealthBriefing:
    """Executive briefing: score, trend, top risks, and AI-generated narrative.

    Calls Claude to write a 3-sentence briefing if ANTHROPIC_API_KEY is set.
    Falls back to a deterministic template if the key is absent so the
    endpoint remains functional in network-isolated deployments.

    Typical latency with Claude: 2–4 seconds.
    """
    from graph.org_health import generate_briefing

    current = _resolve_health(conn)
    trend   = queries.fetch_org_health_trend(trend_weeks, conn)

    try:
        briefing = await asyncio.to_thread(generate_briefing, current, trend)
    except Exception as exc:
        logger.exception("Briefing generation failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Briefing generation failed: {exc}",
        ) from exc

    return OrgHealthBriefing(
        computed_at=briefing["computed_at"],
        score=briefing["score"],
        tier=briefing["tier"],
        trend_delta=briefing["trend_delta"],
        trend_direction=briefing["trend_direction"],
        top_risks=[RiskFactor(**r) for r in briefing["top_risks"]],
        recommended_actions=briefing["recommended_actions"],
        narrative=briefing["narrative"],
    )
