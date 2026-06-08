"""Router: /scenarios — Reorg Scenario Planner (Feature 4)."""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_db, require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scenarios", tags=["scenarios"])

# ─── Request / response models ────────────────────────────────────────────────


class ScenarioOperation(BaseModel):
    op: Literal["remove", "merge_depts", "move_team"]
    employee_ids: list[str] | None = None
    source_dept: str | None = None
    target_dept: str | None = None


class ScenarioCreateRequest(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = None
    operations: list[ScenarioOperation] = Field(min_length=1)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _validate_operations(ops: list[ScenarioOperation]) -> list[dict]:
    result = []
    for op in ops:
        d = op.model_dump(exclude_none=True)
        if op.op == "remove" and not op.employee_ids:
            raise HTTPException(status_code=422, detail="'remove' operation requires employee_ids.")
        if op.op == "merge_depts" and (not op.source_dept or not op.target_dept):
            raise HTTPException(status_code=422, detail="'merge_depts' requires source_dept and target_dept.")
        if op.op == "move_team" and (not op.employee_ids or not op.target_dept):
            raise HTTPException(status_code=422, detail="'move_team' requires employee_ids and target_dept.")
        result.append(d)
    return result


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("", status_code=201, summary="Create a new reorg scenario")
def create_scenario(
    body: ScenarioCreateRequest,
    conn=Depends(get_db),
    _: str = Depends(require_role("hr_admin")),
) -> dict:
    ops = _validate_operations(body.operations)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reorg_scenarios (name, description, operations, status)
            VALUES (%s, %s, %s::jsonb, 'draft')
            RETURNING id::text, created_at
            """,
            (body.name, body.description, json.dumps(ops)),
        )
        row = cur.fetchone()
    return {"scenario_id": row["id"], "status": "draft", "created_at": str(row["created_at"])}


@router.post("/{scenario_id}/compute", summary="Run the simulation for a scenario")
def compute_scenario(
    scenario_id: str,
    conn=Depends(get_db),
    _: str = Depends(require_role("hr_admin")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, operations FROM reorg_scenarios WHERE id = %s::uuid AND status != 'archived'",
            (scenario_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found.")

    ops = row["operations"] or []

    try:
        from graph.scenario_simulator import load_current_graph, apply_operations, compute_impact_report

        G_before = load_current_graph(conn)
        G_after = apply_operations(G_before, ops)
        impact = compute_impact_report(G_before, G_after, conn)
    except Exception as exc:
        logger.exception("Scenario simulation failed: %s", exc)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reorg_scenarios SET status='draft' WHERE id = %s::uuid",
                (scenario_id,),
            )
        raise HTTPException(status_code=502, detail=f"Simulation failed: {exc}") from exc

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE reorg_scenarios
               SET status='computed', impact_report=%s::jsonb, computed_at=NOW()
             WHERE id = %s::uuid
            """,
            (json.dumps(impact), scenario_id),
        )

    return {"scenario_id": scenario_id, "status": "computed", "impact_report": impact}


@router.get("", summary="List all non-archived scenarios")
def list_scenarios(conn=Depends(get_db)) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, name, description, status, created_at, computed_at,
                   impact_report->'avg_path_length_delta_pct' AS path_delta,
                   impact_report->'nodes_removed' AS nodes_removed
            FROM reorg_scenarios
            WHERE status != 'archived'
            ORDER BY created_at DESC
            LIMIT 50
            """,
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "total": len(rows),
        "scenarios": [
            {
                "scenario_id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "status": r["status"],
                "created_at": str(r["created_at"]),
                "computed_at": str(r["computed_at"]) if r["computed_at"] else None,
                "path_length_delta_pct": r["path_delta"],
                "nodes_removed": r["nodes_removed"],
            }
            for r in rows
        ],
    }


@router.get("/compare", summary="Compare up to 4 scenarios side-by-side")
def compare_scenarios(
    ids: str = Query(description="Comma-separated scenario UUIDs (max 4)"),
    conn=Depends(get_db),
) -> dict:
    scenario_ids = [s.strip() for s in ids.split(",") if s.strip()][:4]
    if not scenario_ids:
        raise HTTPException(status_code=422, detail="Provide at least one scenario ID.")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, name, status, impact_report
            FROM reorg_scenarios
            WHERE id = ANY(%s::uuid[]) AND status = 'computed'
            ORDER BY created_at DESC
            """,
            (scenario_ids,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "scenarios": [
            {
                "scenario_id": r["id"],
                "name": r["name"],
                "impact_report": r["impact_report"],
            }
            for r in rows
        ]
    }


@router.get("/{scenario_id}", summary="Get full scenario detail")
def get_scenario(scenario_id: str, conn=Depends(get_db)) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, name, description, operations, status,
                   impact_report, created_at, computed_at
            FROM reorg_scenarios
            WHERE id = %s::uuid
            """,
            (scenario_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found.")

    row = dict(row)
    return {
        "scenario_id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "operations": row["operations"],
        "status": row["status"],
        "impact_report": row["impact_report"],
        "created_at": str(row["created_at"]),
        "computed_at": str(row["computed_at"]) if row["computed_at"] else None,
    }


@router.delete("/{scenario_id}", status_code=204, response_model=None)
def archive_scenario(
    scenario_id: str,
    conn=Depends(get_db),
    _: str = Depends(require_role("hr_admin")),
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE reorg_scenarios SET status='archived' WHERE id = %s::uuid RETURNING id",
            (scenario_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found.")
