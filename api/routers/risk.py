"""Router: /risk — SPOF scores, critical nodes, history, and What-If simulation."""

import logging
import os
from datetime import date

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException, Query

from api import db as queries
from api.deps import get_db
from graph.builder import build_graph, load_raw_edges
from api.models.schemas import (
    EmployeeRiskHistory,
    EmployeeRiskPoint,
    GraphHealthStats,
    RiskScore,
    RiskScoresResponse,
    SimulateRequest,
    SimulateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])

_SPOF_CRITICAL_THRESHOLD = float(os.environ.get("SPOF_CRITICAL_THRESHOLD", "0.7"))
_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))


def _resolve_date(requested: date | None, conn) -> date:
    if requested is not None:
        return requested
    latest = queries.fetch_latest_snapshot_date(conn)
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="No risk scores found. Run the graph_builder_dag first.",
        )
    return latest


def _graph_health_stats(G: nx.DiGraph) -> GraphHealthStats:
    """Extract summary stats from a NetworkX graph."""
    from graph.metrics import compute_betweenness

    if G.number_of_nodes() == 0:
        return GraphHealthStats(
            node_count=0,
            edge_count=0,
            avg_betweenness=0.0,
            max_betweenness=0.0,
            weakly_connected_components=0,
        )

    betweenness = compute_betweenness(G)
    b_values = list(betweenness.values())
    wcc = nx.number_weakly_connected_components(G)

    return GraphHealthStats(
        node_count=G.number_of_nodes(),
        edge_count=G.number_of_edges(),
        avg_betweenness=round(sum(b_values) / len(b_values), 6) if b_values else 0.0,
        max_betweenness=round(max(b_values), 6) if b_values else 0.0,
        weakly_connected_components=wcc,
    )


@router.get("/scores", response_model=RiskScoresResponse)
def get_risk_scores(
    date: date | None = Query(default=None),
    top: int = Query(default=50, ge=1, le=500),
    conn=Depends(get_db),
) -> RiskScoresResponse:
    """Top-N employees by SPOF score for the given snapshot date.

    Returns the latest scoring run when no date is specified.
    """
    snapshot_date = _resolve_date(date, conn)
    rows = queries.fetch_risk_scores(snapshot_date, top, conn)

    scores = [RiskScore(**r) for r in rows]
    return RiskScoresResponse(
        snapshot_date=snapshot_date,
        total=len(scores),
        scores=scores,
    )


@router.get("/critical-nodes", response_model=RiskScoresResponse)
def get_critical_nodes(
    threshold: float = Query(default=_SPOF_CRITICAL_THRESHOLD, ge=0.0, le=1.0),
    conn=Depends(get_db),
) -> RiskScoresResponse:
    """Employees with SPOF score above threshold from the most recent scoring run.

    Defaults to SPOF_CRITICAL_THRESHOLD env var (default: 0.7).
    """
    rows = queries.fetch_critical_nodes(threshold, conn)
    scores = [RiskScore(**r) for r in rows]

    snapshot_date = scores[0].scored_at if scores else date.today()
    return RiskScoresResponse(
        snapshot_date=snapshot_date,
        total=len(scores),
        scores=scores,
    )


@router.get("/employee/{employee_id}/history", response_model=EmployeeRiskHistory)
def get_employee_risk_history(
    employee_id: str,
    days: int = Query(default=30, ge=1, le=365),
    conn=Depends(get_db),
) -> EmployeeRiskHistory:
    """30-day SPOF score trend for one employee.

    Useful for the EmployeeDetail page sparkline and trend analysis.
    """
    rows = queries.fetch_employee_risk_history(employee_id, days, conn)
    history = [EmployeeRiskPoint(**r) for r in rows]

    return EmployeeRiskHistory(
        employee_id=employee_id,
        days=days,
        history=history,
    )


@router.post("/simulate", response_model=SimulateResponse)
def simulate_removal(
    body: SimulateRequest,
    conn=Depends(get_db),
) -> SimulateResponse:
    """What-If: recalculate graph health after removing one employee.

    Steps:
    1. Load raw_edges for the latest snapshot window.
    2. Build the full collaboration graph.
    3. Compute health stats BEFORE removing the employee.
    4. Remove the employee from the graph.
    5. Compute health stats AFTER.
    6. Return before/after stats and impact deltas.

    Impact dict includes:
        - betweenness_avg_delta (positive = network more stressed)
        - components_delta (positive = more isolated clusters)
        - node_removed_degree (how many edges the removed employee had)
    """
    # Resolve the latest snapshot date to use as the window anchor
    latest_date = queries.fetch_latest_snapshot_date(conn)
    if latest_date is None:
        raise HTTPException(
            status_code=404,
            detail="No graph snapshots found. Run the graph_builder_dag first.",
        )

    raw_edges = load_raw_edges(latest_date, body.window_days)
    if not raw_edges:
        raise HTTPException(
            status_code=404,
            detail=f"No collaboration data found for the {body.window_days}-day window ending {latest_date}.",
        )

    G = build_graph(raw_edges)

    if body.remove_employee_id not in G:
        raise HTTPException(
            status_code=404,
            detail=f"Employee {body.remove_employee_id} not found in the collaboration graph.",
        )

    before_stats = _graph_health_stats(G)
    degree_before = G.degree(body.remove_employee_id)

    G_after = G.copy()
    G_after.remove_node(body.remove_employee_id)
    after_stats = _graph_health_stats(G_after)

    impact = {
        "betweenness_avg_delta": round(
            after_stats.avg_betweenness - before_stats.avg_betweenness, 6
        ),
        "components_delta": after_stats.weakly_connected_components
        - before_stats.weakly_connected_components,
        "node_removed_degree": degree_before,
    }

    logger.info(
        "POST /risk/simulate removed=%s before_nodes=%d after_nodes=%d "
        "components_delta=%d",
        body.remove_employee_id[:8],
        before_stats.node_count,
        after_stats.node_count,
        impact["components_delta"],
    )

    return SimulateResponse(
        removed_employee_id=body.remove_employee_id,
        before=before_stats,
        after=after_stats,
        impact=impact,
    )
