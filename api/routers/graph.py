"""Router: /graph — graph snapshot, ego-network, and community endpoints."""

import logging
import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db as queries
from api.deps import get_db
from api.models.schemas import (
    CommunitiesResponse,
    Community,
    EgoNetwork,
    GraphEdge,
    GraphSnapshot,
    NodeMetrics,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])

_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))


def _resolve_date(requested: date | None, conn) -> date:
    """Return requested date or fall back to the latest available snapshot date."""
    if requested is not None:
        return requested
    latest = queries.fetch_latest_snapshot_date(conn)
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="No graph snapshots found. Run the graph_builder_dag first.",
        )
    return latest


@router.get("/snapshot", response_model=GraphSnapshot)
def get_snapshot(
    date: date | None = Query(default=None, description="Snapshot date YYYY-MM-DD"),
    window_days: int = Query(default=_WINDOW_DAYS, ge=1, le=365),
    conn=Depends(get_db),
) -> GraphSnapshot:
    """Full graph adjacency list with per-node metrics for a given snapshot date.

    Returns the latest available snapshot when no date is specified.
    """
    snapshot_date = _resolve_date(date, conn)

    node_rows = queries.fetch_graph_nodes(snapshot_date, conn)
    if not node_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No graph snapshot found for {snapshot_date}.",
        )

    edge_rows = queries.fetch_graph_edges(snapshot_date, window_days, conn)

    nodes = [NodeMetrics(**r) for r in node_rows]
    edges = [GraphEdge(**r) for r in edge_rows]

    logger.info(
        "GET /graph/snapshot date=%s nodes=%d edges=%d",
        snapshot_date, len(nodes), len(edges),
    )
    return GraphSnapshot(
        snapshot_date=snapshot_date,
        node_count=len(nodes),
        edge_count=len(edges),
        nodes=nodes,
        edges=edges,
    )


@router.get("/employee/{employee_id}", response_model=EgoNetwork)
def get_employee_ego_network(
    employee_id: str,
    date: date | None = Query(default=None),
    window_days: int = Query(default=_WINDOW_DAYS, ge=1, le=365),
    conn=Depends(get_db),
) -> EgoNetwork:
    """2-hop ego-network for a specific employee.

    Returns the employee's own metrics, all direct neighbours, and the edges
    among them for the rolling window ending at the given snapshot date.
    """
    snapshot_date = _resolve_date(date, conn)
    ego = queries.fetch_ego_network(employee_id, snapshot_date, window_days, conn)

    if not ego:
        raise HTTPException(
            status_code=404,
            detail=f"No data for employee {employee_id} on {snapshot_date}.",
        )

    return EgoNetwork(
        employee_id=employee_id,
        snapshot_date=snapshot_date,
        node=NodeMetrics(**ego["node"]),
        neighbors=[NodeMetrics(**n) for n in ego["neighbors"]],
        edges=[GraphEdge(**e) for e in ego["edges"]],
    )


@router.get("/communities", response_model=CommunitiesResponse)
def get_communities(
    date: date | None = Query(default=None),
    conn=Depends(get_db),
) -> CommunitiesResponse:
    """List of all communities with member IDs, departments, and silo flag.

    Communities are detected by the daily Louvain algorithm run in the
    graph_builder_dag. A community is flagged as a silo when it has an
    active (unresolved) silo alert from the last 7 days.
    """
    snapshot_date = _resolve_date(date, conn)
    community_rows = queries.fetch_communities(snapshot_date, conn)

    communities = [Community(**r) for r in community_rows]
    return CommunitiesResponse(
        snapshot_date=snapshot_date,
        community_count=len(communities),
        communities=communities,
    )
