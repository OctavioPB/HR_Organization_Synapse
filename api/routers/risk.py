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
    ChurnScore,
    ChurnScoresResponse,
    EmployeeChurnDetail,
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


def _cross_dept_edges(G: nx.DiGraph) -> int:
    """Count edges that cross department boundaries."""
    return sum(
        1 for u, v in G.edges()
        if G.nodes[u].get("department") != G.nodes[v].get("department")
    )


def _avg_path_length(G: nx.DiGraph) -> float:
    """Average shortest path length on the largest weakly connected component (undirected).

    Uses the undirected projection to avoid issues with directed reachability.
    Returns 0.0 when the graph has fewer than 2 nodes.
    """
    U = G.to_undirected()
    if U.number_of_nodes() < 2:
        return 0.0
    largest_cc = max(nx.connected_components(U), key=len)
    sub = U.subgraph(largest_cc)
    if sub.number_of_nodes() < 2:
        return 0.0
    return round(nx.average_shortest_path_length(sub), 3)


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
        - cross_dept_loss_pct: % of cross-department edges that disappear
        - avg_path_increase: increase in avg shortest path between colleagues
        - components_delta: new isolated clusters formed (positive = splits)
        - node_removed_degree: direct collaboration links this employee held
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
    cross_before = _cross_dept_edges(G)
    path_before = _avg_path_length(G)

    G_after = G.copy()
    G_after.remove_node(body.remove_employee_id)
    after_stats = _graph_health_stats(G_after)
    cross_after = _cross_dept_edges(G_after)
    path_after = _avg_path_length(G_after)

    cross_dept_loss_pct = round(
        (cross_before - cross_after) / max(cross_before, 1) * 100, 1
    )

    impact = {
        "components_delta": after_stats.weakly_connected_components
            - before_stats.weakly_connected_components,
        "node_removed_degree": degree_before,
        "cross_dept_edges_before": cross_before,
        "cross_dept_edges_after": cross_after,
        "cross_dept_loss_pct": cross_dept_loss_pct,
        "avg_path_length_before": path_before,
        "avg_path_length_after": path_after,
        "avg_path_increase": round(path_after - path_before, 3),
        "max_betweenness_before": round(before_stats.max_betweenness, 4),
        "max_betweenness_after": round(after_stats.max_betweenness, 4),
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


# ─── Churn risk ───────────────────────────────────────────────────────────────


@router.get("/churn-scores", response_model=ChurnScoresResponse)
def get_churn_scores(
    date: date | None = Query(default=None),
    top: int = Query(default=50, ge=1, le=500),
    min_prob: float = Query(default=0.0, ge=0.0, le=1.0),
    conn=Depends(get_db),
) -> ChurnScoresResponse:
    """Churn risk scores for all employees from the most recent GNN scoring run.

    Args:
        date: Specific scored_at date.  Defaults to the latest available.
        top: Maximum number of employees to return (sorted by churn_prob desc).
        min_prob: Only include employees with churn_prob >= min_prob.

    Returns 404 when no scoring run has been executed yet.
    """
    scored_at = date
    if scored_at is None:
        scored_at = queries.fetch_latest_churn_date(conn)
        if scored_at is None:
            raise HTTPException(
                status_code=404,
                detail="No churn scores found. Run the churn_gnn_score DAG first.",
            )

    rows = queries.fetch_churn_scores(scored_at, top, min_prob, conn)
    scores = [ChurnScore(**r) for r in rows]
    return ChurnScoresResponse(scored_at=scored_at, total=len(scores), scores=scores)


@router.get("/employee/{employee_id}/churn", response_model=EmployeeChurnDetail)
def get_employee_churn(
    employee_id: str,
    conn=Depends(get_db),
) -> EmployeeChurnDetail:
    """Full churn score history for a single employee (up to 90 days).

    Returns 404 when the employee has no churn scores on record.
    """
    rows = queries.fetch_employee_churn_history(employee_id, conn)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No churn scores found for employee {employee_id}.",
        )

    history = [ChurnScore(**r) for r in rows]
    latest = history[0]
    return EmployeeChurnDetail(
        employee_id=employee_id,
        name=latest.name,
        department=latest.department,
        latest_churn_prob=latest.churn_prob,
        latest_risk_tier=latest.risk_tier,
        history=history,
    )
