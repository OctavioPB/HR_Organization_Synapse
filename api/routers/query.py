"""POST /query/natural — natural-language HR intelligence endpoint (F7).

Receives a plain-English question, runs the Claude agentic loop with
org-graph tools, and returns a structured answer with a tool-call trace.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_db
from api.models.schemas import NLQueryRequest, NLQueryResponse, ToolCall

router = APIRouter(prefix="/query", tags=["query"])
logger = logging.getLogger(__name__)


@router.post("/natural", response_model=NLQueryResponse)
async def natural_language_query(
    body: NLQueryRequest,
    conn=Depends(get_db),
) -> NLQueryResponse:
    """Answer an HR organisation question using Claude + live graph data.

    Claude decides which tools to call (SPOF scores, silo alerts, simulations,
    knowledge risk, churn risk, succession plans, temporal anomalies) and
    returns a plain-language answer alongside a trace of every tool invocation.

    Typical latency: 2–8 seconds depending on tool count and model response time.
    """
    from api.nl.agent import run_query

    try:
        result = await asyncio.to_thread(run_query, body.question, conn)
    except Exception as exc:
        logger.exception("NL query failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI query failed: {exc}",
        ) from exc

    return NLQueryResponse(
        answer=result["answer"],
        tools_used=[ToolCall(**tc) for tc in result["tools_used"]],
        model=result["model"],
        turns=result["turns"],
        latency_ms=result["latency_ms"],
    )
