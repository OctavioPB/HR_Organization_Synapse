"""Graph metrics computation and persistence.

Public functions:
    compute_betweenness(G)         → {employee_id: float}
    compute_degree_centrality(G)   → ({id: float}, {id: float})  # in, out
    compute_clustering(G)          → {employee_id: float}
    compute_community(G)           → {employee_id: int}
    compute_cross_dept_ratio(G)    → {employee_id: float}
    write_snapshot(snapshot_date, G, metrics, conn) → None

Performance:
    Graphs with > BETWEENNESS_EXACT_THRESHOLD nodes use approximate betweenness
    (Brandes k-pivot sampling) to stay within the 30-second DAG budget.
    Community detection runs in parallel across CPU cores when joblib is available.

CLI:
    python graph/metrics.py --snapshot-date 2025-04-25
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import networkx as nx
import psycopg2
import psycopg2.extras

try:
    import community as community_louvain
    _LOUVAIN_AVAILABLE = True
except ImportError:
    _LOUVAIN_AVAILABLE = False

try:
    from joblib import Parallel, delayed
    _JOBLIB_AVAILABLE = True
except ImportError:
    _JOBLIB_AVAILABLE = False

# Graphs larger than this use k-pivot approximate betweenness (O(k·n²) vs O(n³))
BETWEENNESS_EXACT_THRESHOLD = int(os.environ.get("BETWEENNESS_EXACT_THRESHOLD", "500"))
# Number of pivot nodes for approximate betweenness (higher k = more accurate, slower)
BETWEENNESS_K_PIVOTS = int(os.environ.get("BETWEENNESS_K_PIVOTS", "200"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.builder import build_graph, load_raw_edges
from ingestion.db import get_conn

logger = logging.getLogger(__name__)


# ─── Metric computation ───────────────────────────────────────────────────────


def compute_betweenness(G: nx.DiGraph) -> dict[str, float]:
    """Compute normalised betweenness centrality on the directed graph.

    Uses edge weights (higher weight = shorter path via weight inversion).
    Returns 0.0 for all nodes in graphs with fewer than 3 nodes.

    For graphs larger than BETWEENNESS_EXACT_THRESHOLD nodes, uses Brandes
    k-pivot approximate algorithm (k=BETWEENNESS_K_PIVOTS). Error is bounded
    by O(1/sqrt(k)) — with k=200 the typical error is < 1% for org graphs.

    Args:
        G: Directed weighted collaboration graph.

    Returns:
        Dict mapping employee_id → betweenness centrality ∈ [0, 1].
    """
    n = G.number_of_nodes()
    if n < 3:
        return {node: 0.0 for node in G.nodes()}

    # Invert weights so higher interaction frequency = shorter path
    G_inv = G.copy()
    for u, v, data in G_inv.edges(data=True):
        G_inv[u][v]["inv_weight"] = 1.0 / max(data.get("weight", 1.0), 1e-9)

    if n > BETWEENNESS_EXACT_THRESHOLD:
        k = min(n, BETWEENNESS_K_PIVOTS)
        logger.info(
            "Graph has %d nodes (> %d threshold): using approximate betweenness k=%d",
            n, BETWEENNESS_EXACT_THRESHOLD, k,
        )
        return nx.betweenness_centrality(
            G_inv, normalized=True, weight="inv_weight", k=k, seed=42
        )

    return nx.betweenness_centrality(G_inv, normalized=True, weight="inv_weight")


def compute_degree_centrality(
    G: nx.DiGraph,
) -> tuple[dict[str, float], dict[str, float]]:
    """Compute normalised in-degree and out-degree centrality.

    Args:
        G: Directed collaboration graph.

    Returns:
        Tuple of (in_degree_centrality, out_degree_centrality) dicts.
    """
    return nx.in_degree_centrality(G), nx.out_degree_centrality(G)


def compute_clustering(G: nx.DiGraph) -> dict[str, float]:
    """Compute clustering coefficient on the undirected projection of G.

    Directed clustering is complex to interpret; the undirected projection
    measures how tightly-knit an employee's neighbourhood is.

    Args:
        G: Directed collaboration graph.

    Returns:
        Dict mapping employee_id → clustering coefficient ∈ [0, 1].
    """
    U = G.to_undirected()
    return nx.clustering(U)


def compute_community(
    G: nx.DiGraph,
    random_state: int = 42,
    n_jobs: int = -1,
) -> dict[str, int]:
    """Detect communities using Louvain on the undirected projection.

    For large graphs (> BETWEENNESS_EXACT_THRESHOLD nodes), runs multiple
    Louvain trials in parallel via joblib and returns the partition with the
    highest modularity score. This both exploits available CPU cores and
    reduces sensitivity to Louvain's randomised initialisation.

    Falls back to weakly connected components if python-louvain is not installed.

    Args:
        G: Directed collaboration graph.
        random_state: Base seed; parallel trials use random_state + trial_index.
        n_jobs: Number of parallel jobs (-1 = all CPUs). Ignored when joblib
                is not installed or the graph is below the exact threshold.

    Returns:
        Dict mapping employee_id → community_id (int).
    """
    U = G.to_undirected()

    if not _LOUVAIN_AVAILABLE or U.number_of_nodes() == 0:
        logger.warning("python-louvain not installed — falling back to connected components")
        communities: dict[str, int] = {}
        for comm_id, component in enumerate(nx.connected_components(U)):
            for node in component:
                communities[node] = comm_id
        return communities

    n = U.number_of_nodes()
    if _JOBLIB_AVAILABLE and n > BETWEENNESS_EXACT_THRESHOLD:
        n_trials = min(8, max(2, (n_jobs if n_jobs > 0 else 4)))
        logger.info(
            "Graph has %d nodes: running %d parallel Louvain trials", n, n_trials
        )
        results = Parallel(n_jobs=n_jobs)(
            delayed(community_louvain.best_partition)(U, random_state=random_state + i)
            for i in range(n_trials)
        )
        # Select partition with highest modularity
        best = max(
            results,
            key=lambda p: community_louvain.modularity(p, U),
        )
        return best

    return community_louvain.best_partition(U, random_state=random_state)


def compute_cross_dept_ratio(G: nx.DiGraph) -> dict[str, float]:
    """Compute fraction of each employee's edges that cross department boundaries.

    High cross-dept ratio combined with high betweenness → key bridge employee.

    Args:
        G: Directed graph with 'department' node attributes.

    Returns:
        Dict mapping employee_id → cross_dept_ratio ∈ [0, 1].
        Nodes with no outgoing edges get ratio 0.0.
    """
    ratios: dict[str, float] = {}
    for node in G.nodes():
        node_dept = G.nodes[node].get("department", "")
        out_edges = list(G.out_edges(node))
        if not out_edges:
            ratios[node] = 0.0
            continue
        cross = sum(
            1 for _, target in out_edges
            if G.nodes[target].get("department", "") != node_dept
        )
        ratios[node] = cross / len(out_edges)
    return ratios


# ─── Persistence ──────────────────────────────────────────────────────────────


def write_snapshot(
    snapshot_date: date,
    betweenness: dict[str, float],
    degree_in: dict[str, float],
    degree_out: dict[str, float],
    clustering: dict[str, float],
    communities: dict[str, int],
) -> None:
    """Persist all graph metrics to the graph_snapshots table.

    Uses UPSERT (ON CONFLICT DO UPDATE) so re-running a date is idempotent.

    Args:
        snapshot_date: The date for this snapshot.
        betweenness: Betweenness centrality per employee.
        degree_in: In-degree centrality per employee.
        degree_out: Out-degree centrality per employee.
        clustering: Clustering coefficient per employee.
        communities: Community ID per employee.
    """
    all_nodes = set(betweenness) | set(degree_in) | set(clustering) | set(communities)
    rows = [
        (
            snapshot_date,
            node,
            betweenness.get(node, 0.0),
            degree_in.get(node, 0.0),
            degree_out.get(node, 0.0),
            clustering.get(node, 0.0),
            communities.get(node),
        )
        for node in all_nodes
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO graph_snapshots
                    (snapshot_date, employee_id, betweenness, degree_in, degree_out,
                     clustering, community_id)
                VALUES (%s, %s::uuid, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_date, employee_id)
                DO UPDATE SET
                    betweenness  = EXCLUDED.betweenness,
                    degree_in    = EXCLUDED.degree_in,
                    degree_out   = EXCLUDED.degree_out,
                    clustering   = EXCLUDED.clustering,
                    community_id = EXCLUDED.community_id
                """,
                rows,
                page_size=500,
            )

    logger.info(
        "Wrote %d rows to graph_snapshots for %s", len(rows), snapshot_date
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Compute graph metrics and write to graph_snapshots."
    )
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat, required=True,
        help="Date to compute metrics for (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--window-days", type=int,
        default=int(os.environ.get("GRAPH_WINDOW_DAYS", "30")),
    )
    args = parser.parse_args()

    raw_edges = load_raw_edges(args.snapshot_date, args.window_days)
    G = build_graph(raw_edges)

    if G.number_of_nodes() == 0:
        logger.warning("Graph is empty for %s — nothing to compute.", args.snapshot_date)
        return

    betweenness = compute_betweenness(G)
    degree_in, degree_out = compute_degree_centrality(G)
    clustering = compute_clustering(G)
    communities = compute_community(G)

    top3 = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:3]
    logger.info("Top-3 betweenness: %s", [(n[:8], f"{b:.4f}") for n, b in top3])

    write_snapshot(args.snapshot_date, betweenness, degree_in, degree_out, clustering, communities)
    logger.info("Done for %s.", args.snapshot_date)


if __name__ == "__main__":
    main()
