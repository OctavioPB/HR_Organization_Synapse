"""Turnover-contagion alert layer (MODEL.md §7.4.1).

AlKetbi et al. (2025) show employees are ~23% more likely to depart when >30% of
their immediate peers depart within a six-month window, and that embedding such
a contagion signal improves turnover prediction accuracy by ~30% over
individual-attribute baselines.  The current GNN feature matrix (ml/gnn) captures
individual + 1-hop structural signals but no measure of *peer departure rate*.

Rather than retrain the GNN (which would invalidate existing checkpoints), this
module implements the recommended rule-based alert layer on top of it:

    peer_churn_rate(v, t) = |{u ∈ N(v) : departed(u, t−W, t)}| / max(|N(v)|, 1)

where W = PEER_CHURN_WINDOW_DAYS (default 180).  When peer_churn_rate exceeds
PEER_CONTAGION_THRESHOLD (default 0.30) a supplemental ``peer_contagion_risk``
alert fires, independent of the GNN churn score.  The rate is also persisted onto
the day's ``churn_scores`` rows so the dashboard can surface it alongside the
model score.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

import networkx as nx

logger = logging.getLogger(__name__)

_PEER_CHURN_WINDOW_DAYS = int(os.environ.get("PEER_CHURN_WINDOW_DAYS", "180"))
_PEER_CONTAGION_THRESHOLD = float(os.environ.get("PEER_CONTAGION_THRESHOLD", "0.30"))
_GRAPH_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))


def peer_churn_rate(neighbors: set[str], departed: set[str]) -> float:
    """Fraction of *neighbors* who appear in the *departed* set (MODEL.md §7.4.1).

    Returns 0.0 for an employee with no neighbors (no peers to lose).
    """
    if not neighbors:
        return 0.0
    return len(neighbors & departed) / len(neighbors)


def _undirected_neighbors(G: nx.DiGraph) -> dict[str, set[str]]:
    """Map each node to its undirected neighbor set (predecessors ∪ successors)."""
    return {
        n: set(G.predecessors(n)) | set(G.successors(n))
        for n in G.nodes()
    }


def task_compute_peer_contagion(
    snapshot_date_str: str,
    window_days: int = _GRAPH_WINDOW_DAYS,
    conn=None,
) -> dict:
    """Compute peer_churn_rate per employee and fire peer_contagion_risk alerts.

    Args:
        snapshot_date_str: ISO date string YYYY-MM-DD.
        window_days: Rolling window for building the collaboration graph.
        conn: Optional open psycopg2 connection.  Opens its own if None.

    Returns:
        JSON-serialisable dict with employee_count, flagged_count, threshold.
    """
    from graph.builder import build_graph, load_raw_edges

    snapshot_date = date.fromisoformat(snapshot_date_str)
    depart_start = snapshot_date - timedelta(days=_PEER_CHURN_WINDOW_DAYS)

    raw_edges = load_raw_edges(snapshot_date, window_days)
    G = build_graph(raw_edges)
    if G.number_of_nodes() == 0:
        logger.warning("task_compute_peer_contagion: empty graph for %s", snapshot_date_str)
        return {"snapshot_date": snapshot_date_str, "employee_count": 0, "flagged_count": 0}

    neighbor_map = _undirected_neighbors(G)

    own_conn = conn is None
    if own_conn:
        from ingestion.db import get_conn
        conn = get_conn()

    try:
        # Employees who departed within the trailing contagion window.
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT employee_id::text
                FROM churn_labels
                WHERE churned = true
                  AND label_date BETWEEN %s AND %s
                """,
                (depart_start, snapshot_date),
            )
            departed = {str(r[0]) for r in cur.fetchall()}

        flagged = 0
        with conn.cursor() as cur:
            for emp_id, neighbors in neighbor_map.items():
                rate = peer_churn_rate(neighbors, departed)

                # Persist rate onto today's churn_scores row, if one exists.
                cur.execute(
                    """
                    UPDATE churn_scores
                    SET peer_churn_rate    = %s,
                        peer_contagion_risk = %s
                    WHERE employee_id = %s::uuid AND scored_at = %s
                    """,
                    (round(rate, 4), rate > _PEER_CONTAGION_THRESHOLD, emp_id, snapshot_date),
                )

                if rate > _PEER_CONTAGION_THRESHOLD:
                    flagged += 1
                    cur.execute(
                        """
                        INSERT INTO alerts (type, severity, affected_entities, details)
                        VALUES ('peer_contagion_risk', 'high', %s::jsonb, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            json.dumps({
                                "employee_id": emp_id,
                                "peer_churn_rate": round(rate, 4),
                                "departed_peers": len(neighbors & departed),
                                "total_peers": len(neighbors),
                                "snapshot_date": snapshot_date_str,
                            }),
                            f"Employee {emp_id[:8]}… has {round(rate * 100)}% of peers departed "
                            f"in the last {_PEER_CHURN_WINDOW_DAYS} days "
                            f"(> {round(_PEER_CONTAGION_THRESHOLD * 100)}% contagion threshold).",
                        ),
                    )

        conn.commit()
    finally:
        if own_conn:
            conn.close()

    stats = {
        "snapshot_date": snapshot_date_str,
        "employee_count": len(neighbor_map),
        "flagged_count": flagged,
        "threshold": _PEER_CONTAGION_THRESHOLD,
    }
    logger.info("task_compute_peer_contagion: %s", stats)
    return stats
