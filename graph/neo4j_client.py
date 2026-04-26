"""Neo4j client — connection management and graph query helpers.

All public functions fail gracefully when Neo4j is unreachable so callers
can fall back to NetworkX without crashing.

Environment variables:
    NEO4J_URI      bolt://localhost:7687
    NEO4J_USER     neo4j
    NEO4J_PASSWORD changeme
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "changeme")

_driver = None


def get_driver():
    """Return the module-level Neo4j driver, initialising it lazily."""
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            _NEO4J_URI,
            auth=(_NEO4J_USER, _NEO4J_PASSWORD),
            max_connection_pool_size=10,
        )
    return _driver


def close_driver() -> None:
    """Close the module-level driver. Called on application shutdown."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def neo4j_available() -> bool:
    """Return True if Neo4j is reachable, False otherwise."""
    try:
        get_driver().verify_connectivity()
        return True
    except Exception as exc:
        logger.debug("Neo4j not available: %s", exc)
        return False


# ─── Write operations ─────────────────────────────────────────────────────────


def upsert_graph(
    snapshot_date: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, int]:
    """Upsert a full graph snapshot into Neo4j.

    Args:
        snapshot_date: ISO date string (YYYY-MM-DD). Stored on relationships.
        nodes: List of dicts with keys: employee_id, name, department, spof_score.
        edges: List of dicts with keys: source_id, target_id, weight.

    Returns:
        Dict with 'nodes_upserted' and 'edges_upserted' counts.
    """
    driver = get_driver()
    with driver.session() as session:
        session.run(
            """
            UNWIND $nodes AS n
            MERGE (e:Employee {id: n.employee_id})
            SET e.name          = n.name,
                e.department    = n.department,
                e.spof_score    = n.spof_score,
                e.updated_at    = datetime()
            """,
            nodes=nodes,
        )

        edges_with_date = [{**e, "snapshot_date": snapshot_date} for e in edges]
        session.run(
            """
            UNWIND $edges AS edge
            MATCH (a:Employee {id: edge.source_id})
            MATCH (b:Employee {id: edge.target_id})
            MERGE (a)-[r:INTERACTED_WITH {snapshot_date: edge.snapshot_date}]->(b)
            SET r.weight = edge.weight
            """,
            edges=edges_with_date,
        )

    logger.info(
        "Neo4j upsert complete — %d nodes, %d edges (snapshot %s)",
        len(nodes), len(edges), snapshot_date,
    )
    return {"nodes_upserted": len(nodes), "edges_upserted": len(edges)}


def ensure_indexes() -> None:
    """Create uniqueness constraint and indexes if they don't exist yet."""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT employee_id IF NOT EXISTS "
            "FOR (e:Employee) REQUIRE e.id IS UNIQUE"
        )
        session.run(
            "CREATE INDEX employee_dept IF NOT EXISTS "
            "FOR (e:Employee) ON (e.department)"
        )
    logger.info("Neo4j indexes ensured.")


# ─── Read operations ──────────────────────────────────────────────────────────


def query_shortest_path(
    from_id: str,
    to_id: str,
    max_hops: int = 6,
) -> dict[str, Any] | None:
    """Find the shortest undirected collaboration path between two employees.

    Args:
        from_id: Source employee UUID.
        to_id: Target employee UUID.
        max_hops: Maximum path length to consider (default: 6).

    Returns:
        Dict with 'path' (list of node dicts) and 'hops' (int), or None if
        no path exists within max_hops.
    """
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            f"""
            MATCH path = shortestPath(
                (a:Employee {{id: $from_id}})-[:INTERACTED_WITH*1..{max_hops}]-(b:Employee {{id: $to_id}})
            )
            RETURN
                [node IN nodes(path) | node.id]         AS node_ids,
                [node IN nodes(path) | node.name]        AS names,
                [node IN nodes(path) | node.department]  AS departments,
                length(path) AS hops
            LIMIT 1
            """,
            from_id=from_id,
            to_id=to_id,
        )
        record = result.single()

    if record is None:
        return None

    return {
        "path": [
            {
                "employee_id": nid,
                "name": name,
                "department": dept,
            }
            for nid, name, dept in zip(
                record["node_ids"], record["names"], record["departments"]
            )
        ],
        "hops": record["hops"],
    }


def query_reachability(
    employee_id: str,
    hops: int = 2,
) -> list[dict[str, Any]]:
    """Find all employees reachable from the given node within N undirected hops.

    Args:
        employee_id: Source employee UUID.
        hops: Maximum number of hops (default: 2, max: 4).

    Returns:
        List of dicts with employee_id, name, department, spof_score.
    """
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (source:Employee {{id: $employee_id}})-[:INTERACTED_WITH*1..{hops}]-(neighbor:Employee)
            WHERE neighbor.id <> $employee_id
            RETURN DISTINCT
                neighbor.id          AS employee_id,
                neighbor.name        AS name,
                neighbor.department  AS department,
                neighbor.spof_score  AS spof_score
            ORDER BY neighbor.name
            """,
            employee_id=employee_id,
        )
        return [dict(r) for r in result]


def query_knowledge_islands(max_size: int = 2) -> list[dict[str, Any]]:
    """Find employees with very few collaboration connections (knowledge islands).

    A knowledge island is an employee with <= max_size total undirected
    collaboration partners in the current graph state.

    Args:
        max_size: Connection count threshold (default: 2).

    Returns:
        List of dicts with employee_id, name, department, connection_count.
    """
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:Employee)
            OPTIONAL MATCH (e)-[:INTERACTED_WITH]-(neighbor:Employee)
            WITH e, count(DISTINCT neighbor) AS connection_count
            WHERE connection_count <= $max_size
            RETURN
                e.id         AS employee_id,
                e.name       AS name,
                e.department AS department,
                connection_count
            ORDER BY connection_count ASC, e.department ASC
            """,
            max_size=max_size,
        )
        return [dict(r) for r in result]


def query_betweenness_gds(graph_name: str = "orgGraph") -> list[dict[str, Any]]:
    """Compute betweenness centrality via Neo4j GDS plugin.

    This is preferred over NetworkX for graphs with > 500 nodes.
    Requires the graph-data-science plugin to be installed.

    Args:
        graph_name: Name for the in-memory GDS graph projection.

    Returns:
        List of dicts with employee_id and betweenness score, sorted desc.
        Returns an empty list if GDS is unavailable.
    """
    driver = get_driver()
    try:
        with driver.session() as session:
            session.run(
                """
                CALL gds.graph.project(
                    $graph_name,
                    'Employee',
                    {INTERACTED_WITH: {orientation: 'UNDIRECTED'}}
                )
                """,
                graph_name=graph_name,
            )
            result = session.run(
                """
                CALL gds.betweenness.stream($graph_name)
                YIELD nodeId, score
                MATCH (e:Employee) WHERE id(e) = nodeId
                RETURN e.id AS employee_id, score AS betweenness
                ORDER BY score DESC
                """,
                graph_name=graph_name,
            )
            rows = [dict(r) for r in result]

        with driver.session() as session:
            session.run("CALL gds.graph.drop($graph_name)", graph_name=graph_name)

        logger.info("GDS betweenness computed for %d nodes", len(rows))
        return rows

    except Exception as exc:
        logger.warning(
            "GDS betweenness failed (plugin may not be installed): %s", exc
        )
        return []
