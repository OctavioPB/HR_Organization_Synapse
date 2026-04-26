"""Router: /graph — graph snapshot, ego-network, community, and path endpoints."""

import logging
import os
from datetime import date

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException, Query

from api import cache, db as queries
from api.deps import get_db
from api.models.schemas import (
    CommunitiesResponse,
    Community,
    EgoNetwork,
    GraphEdge,
    GraphSnapshot,
    KnowledgeIsland,
    KnowledgeIslandsResponse,
    NodeMetrics,
    PathNode,
    ReachabilityResponse,
    ReachableEmployee,
    ShortestPathResponse,
)
from graph.builder import build_graph, load_raw_edges

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
    Responses are cached in Redis for CACHE_TTL_SEC seconds (default 1 hour).
    Cache is automatically invalidated when graph_builder_dag writes a new snapshot.
    """
    snapshot_date = _resolve_date(date, conn)
    cache_key = cache.make_key("snapshot", str(snapshot_date), str(window_days))

    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug("GET /graph/snapshot cache HIT date=%s", snapshot_date)
        return GraphSnapshot(**cached)

    node_rows = queries.fetch_graph_nodes(snapshot_date, conn)
    if not node_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No graph snapshot found for {snapshot_date}.",
        )

    edge_rows = queries.fetch_graph_edges(snapshot_date, window_days, conn)

    nodes = [NodeMetrics(**r) for r in node_rows]
    edges = [GraphEdge(**r) for r in edge_rows]

    response = GraphSnapshot(
        snapshot_date=snapshot_date,
        node_count=len(nodes),
        edge_count=len(edges),
        nodes=nodes,
        edges=edges,
    )
    cache.set(cache_key, response.model_dump(mode="json"))

    logger.info(
        "GET /graph/snapshot date=%s nodes=%d edges=%d",
        snapshot_date, len(nodes), len(edges),
    )
    return response


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


@router.get("/path", response_model=ShortestPathResponse)
def get_shortest_path(
    from_employee_id: str = Query(..., description="Source employee UUID"),
    to_employee_id: str = Query(..., description="Target employee UUID"),
    conn=Depends(get_db),
) -> ShortestPathResponse:
    """Shortest collaboration path between two employees.

    Queries Neo4j when available; falls back to NetworkX if Neo4j is unreachable.
    Returns 404 if the two employees are not connected within 6 hops.
    """
    from graph.neo4j_client import neo4j_available, query_shortest_path

    if neo4j_available():
        result = query_shortest_path(from_employee_id, to_employee_id)
        source = "neo4j"
    else:
        result = _nx_shortest_path(from_employee_id, to_employee_id, conn)
        source = "networkx"

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No collaboration path found between {from_employee_id[:8]}… "
                f"and {to_employee_id[:8]}… within 6 hops."
            ),
        )

    logger.info(
        "GET /graph/path source=%s hops=%d from=%s to=%s",
        source, result["hops"], from_employee_id[:8], to_employee_id[:8],
    )
    return ShortestPathResponse(
        from_employee_id=from_employee_id,
        to_employee_id=to_employee_id,
        path=[PathNode(**n) for n in result["path"]],
        hops=result["hops"],
        source=source,
    )


@router.get("/reachability/{employee_id}", response_model=ReachabilityResponse)
def get_reachability(
    employee_id: str,
    hops: int = Query(default=2, ge=1, le=4, description="Maximum hop distance"),
    conn=Depends(get_db),
) -> ReachabilityResponse:
    """All employees reachable from the given employee within N undirected hops.

    Queries Neo4j when available; falls back to NetworkX if Neo4j is unreachable.
    Useful for understanding an employee's extended influence or isolation.
    """
    from graph.neo4j_client import neo4j_available, query_reachability

    if neo4j_available():
        reachable = query_reachability(employee_id, hops)
        source = "neo4j"
    else:
        reachable = _nx_reachability(employee_id, hops, conn)
        source = "networkx"

    logger.info(
        "GET /graph/reachability/%s hops=%d reachable=%d source=%s",
        employee_id[:8], hops, len(reachable), source,
    )
    return ReachabilityResponse(
        employee_id=employee_id,
        hops=hops,
        reachable_count=len(reachable),
        reachable=[ReachableEmployee(**r) for r in reachable],
        source=source,
    )


@router.get("/knowledge-islands", response_model=KnowledgeIslandsResponse)
def get_knowledge_islands(
    max_size: int = Query(default=2, ge=1, le=10, description="Max connection count to qualify"),
    conn=Depends(get_db),
) -> KnowledgeIslandsResponse:
    """Employees with very few collaboration connections (knowledge islands).

    Identifies employees who interact with at most max_size unique colleagues,
    making them potential knowledge silos or at-risk of isolation.
    Queries Neo4j when available; falls back to NetworkX if unreachable.
    """
    from graph.neo4j_client import neo4j_available, query_knowledge_islands

    if neo4j_available():
        islands = query_knowledge_islands(max_size)
        source = "neo4j"
    else:
        islands = _nx_knowledge_islands(max_size, conn)
        source = "networkx"

    logger.info(
        "GET /graph/knowledge-islands max_size=%d found=%d source=%s",
        max_size, len(islands), source,
    )
    return KnowledgeIslandsResponse(
        total=len(islands),
        max_size=max_size,
        islands=[KnowledgeIsland(**i) for i in islands],
        source=source,
    )


# ─── NetworkX fallback helpers ────────────────────────────────────────────────


def _load_latest_graph(conn):
    """Load the most recent graph snapshot from PostgreSQL via NetworkX."""
    snapshot_date = queries.fetch_latest_snapshot_date(conn)
    if snapshot_date is None:
        raise HTTPException(
            status_code=404,
            detail="No graph snapshots found. Run the graph_builder_dag first.",
        )
    raw_edges = load_raw_edges(snapshot_date, _WINDOW_DAYS)
    return build_graph(raw_edges)


def _nx_shortest_path(from_id: str, to_id: str, conn) -> dict | None:
    G = _load_latest_graph(conn)
    G_undirected = G.to_undirected()
    try:
        path_nodes = nx.shortest_path(G_undirected, from_id, to_id)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return {
        "path": [
            {
                "employee_id": n,
                "name": None,
                "department": G.nodes[n].get("department") if n in G else None,
            }
            for n in path_nodes
        ],
        "hops": len(path_nodes) - 1,
    }


def _nx_reachability(employee_id: str, hops: int, conn) -> list[dict]:
    G = _load_latest_graph(conn)
    if employee_id not in G:
        return []
    ego = nx.ego_graph(G.to_undirected(), employee_id, radius=hops)
    return [
        {
            "employee_id": n,
            "name": None,
            "department": G.nodes[n].get("department") if n in G else None,
            "spof_score": None,
        }
        for n in ego.nodes()
        if n != employee_id
    ]


def _nx_knowledge_islands(max_size: int, conn) -> list[dict]:
    G = _load_latest_graph(conn)
    G_undirected = G.to_undirected()
    islands = []
    for node in G_undirected.nodes():
        connection_count = G_undirected.degree(node)
        if connection_count <= max_size:
            islands.append(
                {
                    "employee_id": node,
                    "name": None,
                    "department": G.nodes[node].get("department"),
                    "connection_count": connection_count,
                }
            )
    islands.sort(key=lambda x: x["connection_count"])
    return islands
