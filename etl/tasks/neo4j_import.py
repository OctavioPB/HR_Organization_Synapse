"""Task callables for Neo4j graph import — invoked by neo4j_import_dag.

These functions have no Airflow dependencies so they can be unit-tested
without a running Airflow or Neo4j instance.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def task_ensure_indexes() -> dict:
    """Create Neo4j uniqueness constraints and indexes if absent.

    Safe to call repeatedly — uses CREATE IF NOT EXISTS semantics.

    Returns:
        Status dict.
    """
    from graph.neo4j_client import ensure_indexes

    ensure_indexes()
    logger.info("Neo4j indexes verified.")
    return {"status": "indexes_ok"}


def task_import_graph(snapshot_date_str: str, window_days: int = 30) -> dict:
    """Import today's graph snapshot and SPOF scores into Neo4j.

    Reads from:
        - graph_snapshots + employees (node properties)
        - risk_scores (latest SPOF score per employee)
        - raw_events via load_raw_edges (edge weight)

    Returns:
        Dict with nodes_upserted and edges_upserted counts.
    """
    from graph.builder import build_graph, load_raw_edges
    from graph.neo4j_client import upsert_graph
    from ingestion.db import get_conn

    d = date.fromisoformat(snapshot_date_str)

    # ── Load node data ──────────────────────────────────────────────────────
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    e.id::text           AS employee_id,
                    e.name,
                    e.department,
                    COALESCE(
                        (SELECT spof_score FROM risk_scores rs
                         WHERE rs.employee_id = e.id
                         ORDER BY rs.scored_at DESC LIMIT 1),
                        0.0
                    )                    AS spof_score
                FROM graph_snapshots gs
                JOIN employees e ON gs.employee_id = e.id
                WHERE gs.snapshot_date = %s
                """,
                (d,),
            )
            nodes = [
                {
                    "employee_id": row["employee_id"],
                    "name": row["name"],
                    "department": row["department"],
                    "spof_score": float(row["spof_score"]),
                }
                for row in cur.fetchall()
            ]

    if not nodes:
        logger.warning(
            "No graph_snapshot rows for %s — skipping Neo4j import.", snapshot_date_str
        )
        return {"nodes_upserted": 0, "edges_upserted": 0}

    # ── Load and aggregate edges ────────────────────────────────────────────
    raw_edges = load_raw_edges(d, window_days)

    edge_weights: dict[tuple[str, str], float] = {}
    for src, tgt, weight, _, _ in raw_edges:
        edge_weights[(src, tgt)] = edge_weights.get((src, tgt), 0.0) + weight

    edges = [
        {"source_id": src, "target_id": tgt, "weight": round(w, 4)}
        for (src, tgt), w in edge_weights.items()
    ]

    # ── Upsert into Neo4j ───────────────────────────────────────────────────
    result = upsert_graph(snapshot_date_str, nodes, edges)
    logger.info(
        "task_import_graph %s: %d nodes, %d edges upserted",
        snapshot_date_str, result["nodes_upserted"], result["edges_upserted"],
    )
    return result


def task_verify_import(snapshot_date_str: str) -> dict:
    """Verify that the Neo4j import is consistent with the PostgreSQL snapshot.

    Compares node count in Neo4j against the graph_snapshots table.
    Logs a warning (does not fail) if counts differ — Neo4j may have
    nodes from older snapshots that haven't been pruned.

    Returns:
        Dict with neo4j_count, postgres_count, and match flag.
    """
    from graph.neo4j_client import get_driver
    from ingestion.db import get_conn

    d = date.fromisoformat(snapshot_date_str)

    # Count nodes in PostgreSQL for this snapshot
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM graph_snapshots WHERE snapshot_date = %s",
                (d,),
            )
            postgres_count = cur.fetchone()[0]

    # Count nodes in Neo4j
    driver = get_driver()
    with driver.session() as session:
        result = session.run("MATCH (e:Employee) RETURN count(e) AS n")
        neo4j_count = result.single()["n"]

    match = neo4j_count >= postgres_count  # Neo4j accumulates across snapshots
    if not match:
        logger.warning(
            "Neo4j node count (%d) is below PostgreSQL snapshot count (%d) for %s",
            neo4j_count, postgres_count, snapshot_date_str,
        )

    logger.info(
        "verify_import %s: neo4j=%d postgres=%d match=%s",
        snapshot_date_str, neo4j_count, postgres_count, match,
    )
    return {
        "neo4j_count": neo4j_count,
        "postgres_count": postgres_count,
        "match": match,
        "snapshot_date": snapshot_date_str,
    }
