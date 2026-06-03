"""Router: /teams — Team Composition Optimizer (Feature 7)."""

from __future__ import annotations

import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])


class TeamOptimizeRequest(BaseModel):
    departments: list[str] = Field(default_factory=list)
    domains:     list[str] = Field(default_factory=list)
    min_size:    int = Field(default=3, ge=2, le=12)
    max_size:    int = Field(default=6, ge=2, le=20)
    exclude_spof_above: float = Field(default=0.7, ge=0.0, le=1.0)


@router.post("/optimize", summary="Find optimal team compositions")
def optimize_team(
    body: TeamOptimizeRequest,
    conn=Depends(get_db),
) -> dict:
    """Return up to 3 ranked team compositions for the given constraints.

    Scored by: bridge coverage (40%) + domain coverage (35%) + inverse SPOF load (25%).
    """
    if body.min_size > body.max_size:
        raise HTTPException(status_code=422, detail="min_size must be ≤ max_size.")

    try:
        from graph.scenario_simulator import load_current_graph
        from graph.team_optimizer import optimize_team as _optimize

        G = load_current_graph(conn)
        compositions = _optimize(
            G,
            constraints=body.model_dump(),
            conn=conn,
            top_n=3,
        )
    except Exception as exc:
        logger.exception("Team optimization failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Optimization failed: {exc}") from exc

    return {
        "constraints":   body.model_dump(),
        "total_found":   len(compositions),
        "compositions":  compositions,
    }


@router.get("/optimize/export", summary="Export a team composition as CSV")
def export_team_csv(
    composition_index: int = Query(default=0, ge=0, le=2),
    departments: str = Query(default=""),
    domains:     str = Query(default=""),
    min_size:    int = Query(default=3),
    max_size:    int = Query(default=6),
    exclude_spof_above: float = Query(default=0.7),
    conn=Depends(get_db),
) -> StreamingResponse:
    """Re-run optimization and export the Nth composition as CSV."""
    dept_list   = [d.strip() for d in departments.split(",") if d.strip()]
    domain_list = [d.strip() for d in domains.split(",") if d.strip()]

    try:
        from graph.scenario_simulator import load_current_graph
        from graph.team_optimizer import optimize_team as _optimize

        G = load_current_graph(conn)
        compositions = _optimize(
            G,
            constraints={
                "departments": dept_list,
                "domains": domain_list,
                "min_size": min_size,
                "max_size": max_size,
                "exclude_spof_above": exclude_spof_above,
            },
            conn=conn,
            top_n=3,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if composition_index >= len(compositions):
        raise HTTPException(status_code=404, detail=f"Composition {composition_index} not found.")

    comp = compositions[composition_index]
    lines = ["employee_id,name,department,role,spof_score"]
    for m in comp["members"]:
        lines.append(f"{m['employee_id']},{m['name']},{m['department']},{m['role']},{m['spof_score']}")

    csv_content = "\n".join(lines)
    return StreamingResponse(
        io.BytesIO(csv_content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=team_composition_{composition_index + 1}.csv"},
    )


@router.get("/departments", summary="List available departments for team builder")
def list_departments(conn=Depends(get_db)) -> dict:
    """Return distinct department names for use in the team optimizer UI."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT department FROM employees WHERE active = TRUE ORDER BY department"
        )
        depts = [r["department"] for r in cur.fetchall()]
    return {"departments": depts}


@router.get("/domains", summary="List available knowledge domains")
def list_domains(conn=Depends(get_db)) -> dict:
    """Return distinct knowledge domains from employee_knowledge table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT domain FROM employee_knowledge ORDER BY domain"
        )
        domains = [r["domain"] for r in cur.fetchall()]
    return {"domains": domains}
