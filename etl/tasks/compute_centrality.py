"""Task callables for metric computation — invoked by graph_builder_dag.

Plain Python functions, no Airflow dependency, fully unit-testable.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def task_compute_metrics(snapshot_date_str: str, window_days: int = 30) -> dict:
    """Build the graph and compute all per-node metrics, then persist the snapshot.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD (end of rolling window).
        window_days: Rolling window length in days.

    Returns:
        JSON-serialisable stats dict with node_count and community_count.
    """
    from graph.builder import build_graph, load_raw_edges
    from graph.metrics import (
        compute_betweenness,
        compute_clustering,
        compute_community,
        compute_degree_centrality,
        write_snapshot,
    )

    d = date.fromisoformat(snapshot_date_str)
    raw_edges = load_raw_edges(d, window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("task_compute_metrics: graph is empty for %s", snapshot_date_str)
        return {
            "snapshot_date": snapshot_date_str,
            "node_count": 0,
            "community_count": 0,
        }

    betweenness = compute_betweenness(G)
    degree_in, degree_out = compute_degree_centrality(G)
    clustering = compute_clustering(G)
    communities = compute_community(G)

    write_snapshot(d, betweenness, degree_in, degree_out, clustering, communities)

    community_count = len(set(communities.values()))
    stats = {
        "snapshot_date": snapshot_date_str,
        "node_count": G.number_of_nodes(),
        "community_count": community_count,
    }
    logger.info("task_compute_metrics: %s", stats)
    return stats
