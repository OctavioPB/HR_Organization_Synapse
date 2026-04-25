"""Graph builder: reads raw_events from PostgreSQL and constructs a NetworkX DiGraph.

CLI:
    python graph/builder.py --date 2025-04-25
    python graph/builder.py --date 2025-04-25 --window-days 14
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.db import get_conn

logger = logging.getLogger(__name__)


def load_raw_edges(
    snapshot_date: date,
    window_days: int = 30,
) -> list[tuple[str, str, float, str, str]]:
    """Load raw_events from Postgres for the rolling window ending on snapshot_date.

    Only includes events from active employees who have given consent.

    Args:
        snapshot_date: Last day of the window (inclusive, end of day UTC).
        window_days: Number of days in the rolling window.

    Returns:
        List of (source_id, target_id, weight, source_dept, target_dept) tuples.
    """
    end_ts = datetime.combine(snapshot_date, datetime.max.time()).replace(tzinfo=timezone.utc)
    start_ts = end_ts - timedelta(days=window_days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    re.source_id::text,
                    re.target_id::text,
                    re.weight,
                    es.department,
                    et.department
                FROM raw_events re
                JOIN employees es ON re.source_id = es.id
                JOIN employees et ON re.target_id = et.id
                WHERE re.ts BETWEEN %s AND %s
                  AND es.consent = true
                  AND et.consent = true
                  AND es.active   = true
                  AND et.active   = true
                """,
                (start_ts, end_ts),
            )
            rows = cur.fetchall()

    logger.info(
        "Loaded %d raw interactions for window [%s → %s]",
        len(rows), start_ts.date(), snapshot_date,
    )
    return rows


def build_graph(
    raw_edges: list[tuple[str, str, float, str, str]],
) -> nx.DiGraph:
    """Construct a weighted directed graph from raw edge tuples.

    Edge weight = cumulative sum of interaction weights for each directed
    (source, target) pair within the window.
    Node attribute: department (str).

    Args:
        raw_edges: List of (source_id, target_id, weight, source_dept, target_dept).

    Returns:
        nx.DiGraph with weighted edges and 'department' node attributes.
    """
    G = nx.DiGraph()

    edge_weights: dict[tuple[str, str], float] = {}
    node_dept: dict[str, str] = {}

    for source_id, target_id, weight, source_dept, target_dept in raw_edges:
        edge_weights[(source_id, target_id)] = (
            edge_weights.get((source_id, target_id), 0.0) + weight
        )
        node_dept[source_id] = source_dept
        node_dept[target_id] = target_dept

    for node_id, dept in node_dept.items():
        G.add_node(node_id, department=dept)

    for (source, target), weight in edge_weights.items():
        G.add_edge(source, target, weight=weight)

    logger.info(
        "Built DiGraph: %d nodes, %d directed edges (from %d raw interactions)",
        G.number_of_nodes(), G.number_of_edges(), len(raw_edges),
    )
    return G


def graph_to_adjacency(G: nx.DiGraph) -> dict[str, Any]:
    """Serialize a DiGraph to a JSON-compatible adjacency dict.

    Args:
        G: Directed weighted graph.

    Returns:
        Dict with 'nodes' and 'edges' lists, ready for JSON serialisation.
    """
    return {
        "nodes": [
            {"id": n, "department": G.nodes[n].get("department", "")}
            for n in G.nodes()
        ],
        "edges": [
            {"source": u, "target": v, "weight": G[u][v]["weight"]}
            for u, v in G.edges()
        ],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Build collaboration graph from raw_events in Postgres."
    )
    parser.add_argument(
        "--date", type=date.fromisoformat, required=True,
        help="Snapshot date YYYY-MM-DD (end of the rolling window, inclusive)",
    )
    parser.add_argument(
        "--window-days", type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
        help="Rolling window in days (default: GRAPH_WINDOW_DAYS env var or 30)",
    )
    args = parser.parse_args()

    raw_edges = load_raw_edges(args.date, args.window_days)
    G = build_graph(raw_edges)
    logger.info(
        "Graph ready: %d nodes, %d edges",
        G.number_of_nodes(), G.number_of_edges(),
    )


if __name__ == "__main__":
    main()
