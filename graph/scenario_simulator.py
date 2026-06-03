"""Multi-operation reorg scenario simulator.

Applies a list of structural operations to an in-memory NetworkX graph copy
and returns a structured impact report comparing before/after metrics.

Operations supported:
    {"op": "remove",       "employee_ids": [...]}
    {"op": "merge_depts",  "source_dept": "...", "target_dept": "..."}
    {"op": "move_team",    "employee_ids": [...], "target_dept": "..."}
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

_WINDOW_DAYS = int(os.environ.get("GRAPH_WINDOW_DAYS", "30"))
_SILO_THRESHOLD = float(os.environ.get("SILO_THRESHOLD", "4.0"))


# ─── Graph loading ────────────────────────────────────────────────────────────


def load_current_graph(conn, window_days: int = _WINDOW_DAYS) -> nx.DiGraph:
    """Build a NetworkX DiGraph from raw_events for the most recent window."""
    from graph.builder import build_graph

    with conn.cursor() as cur:
        cur.execute("SELECT MAX(snapshot_date) AS d FROM graph_snapshots")
        row = cur.fetchone()
        snap_date = row["d"] if row and row["d"] else date.today()

    end_ts   = f"{snap_date} 23:59:59+00"
    start_ts = (snap_date - timedelta(days=window_days)).isoformat()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT re.source_id::text, re.target_id::text, re.weight,
                   es.department AS dept_source, et.department AS dept_target
            FROM raw_events re
            JOIN employees es ON re.source_id = es.id
            JOIN employees et ON re.target_id = et.id
            WHERE re.ts BETWEEN %s::timestamptz AND %s::timestamptz
              AND es.consent = true AND et.consent = true
              AND es.active = true AND et.active = true
            """,
            (start_ts, end_ts),
        )
        raw_edges = list(cur.fetchall())

    G = build_graph(raw_edges)

    # Attach department attribute to each node
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, department FROM employees WHERE active = true AND consent = true"
        )
        for r in cur.fetchall():
            if r["id"] in G:
                G.nodes[r["id"]]["department"] = r["department"]

    return G


# ─── Operations ───────────────────────────────────────────────────────────────


def apply_operations(G: nx.DiGraph, operations: list[dict]) -> nx.DiGraph:
    """Return a new graph with all operations applied (original is not mutated)."""
    G2 = G.copy()
    for op in operations:
        op_type = op.get("op", "")
        if op_type == "remove":
            emp_ids = op.get("employee_ids", [])
            G2.remove_nodes_from(n for n in emp_ids if n in G2)
        elif op_type == "merge_depts":
            source = op.get("source_dept", "")
            target = op.get("target_dept", "")
            for n, attrs in G2.nodes(data=True):
                if attrs.get("department", "") == source:
                    G2.nodes[n]["department"] = target
        elif op_type == "move_team":
            emp_ids  = op.get("employee_ids", [])
            target_d = op.get("target_dept", "")
            for n in emp_ids:
                if n in G2:
                    G2.nodes[n]["department"] = target_d
        else:
            logger.warning("Unknown scenario operation: %s — skipped.", op_type)
    return G2


# ─── Stats helpers ────────────────────────────────────────────────────────────


def _graph_metrics(G: nx.DiGraph) -> dict:
    if G.number_of_nodes() == 0:
        return {"node_count": 0, "edge_count": 0, "wcc_count": 0, "avg_path_length": 0.0, "avg_betweenness": 0.0}

    U = G.to_undirected()
    wcc_count = nx.number_weakly_connected_components(G)

    # Average betweenness (approximate for large graphs)
    n = G.number_of_nodes()
    if n <= 500:
        bw_dict = nx.betweenness_centrality(U, normalized=True)
    else:
        bw_dict = nx.betweenness_centrality(U, normalized=True, k=min(200, n))
    avg_bw = sum(bw_dict.values()) / max(len(bw_dict), 1)

    # Avg path length (largest component only)
    largest_cc: set[str] = max(nx.connected_components(U), key=len, default=set())
    sub = U.subgraph(largest_cc)
    try:
        avg_path = nx.average_shortest_path_length(sub) if len(sub) > 1 else 0.0
    except Exception:
        avg_path = 0.0

    return {
        "node_count":     G.number_of_nodes(),
        "edge_count":     G.number_of_edges(),
        "wcc_count":      wcc_count,
        "avg_path_length": round(avg_path, 4),
        "avg_betweenness": round(avg_bw, 6),
    }


def _count_silos(G: nx.DiGraph) -> int:
    """Count department silos in the graph (isolation_ratio > SILO_THRESHOLD)."""
    dept_nodes: dict[str, list] = {}
    for n, attrs in G.nodes(data=True):
        d = attrs.get("department", "unknown")
        dept_nodes.setdefault(d, []).append(n)

    silo_count = 0
    for _dept, nodes in dept_nodes.items():
        subgraph = G.subgraph(nodes)
        internal = subgraph.number_of_edges()
        external = sum(
            1 for n in nodes for nb in G.neighbors(n) if nb not in nodes
        )
        ratio = internal / max(external, 1)
        if ratio > _SILO_THRESHOLD:
            silo_count += 1
    return silo_count


# ─── Impact report ────────────────────────────────────────────────────────────


def compute_impact_report(
    G_before: nx.DiGraph,
    G_after: nx.DiGraph,
    conn,
) -> dict[str, Any]:
    """Compare before/after graph metrics and return a structured impact report."""
    metrics_before = _graph_metrics(G_before)
    metrics_after  = _graph_metrics(G_after)
    silos_before   = _count_silos(G_before)
    silos_after    = _count_silos(G_after)

    path_delta_pct = 0.0
    if metrics_before["avg_path_length"] > 0:
        path_delta_pct = round(
            (metrics_after["avg_path_length"] - metrics_before["avg_path_length"])
            / metrics_before["avg_path_length"] * 100,
            1,
        )

    nodes_removed = metrics_before["node_count"] - metrics_after["node_count"]
    new_components = metrics_after["wcc_count"] - metrics_before["wcc_count"]

    # Org health score delta (simple approximation)
    spof_delta = round(
        (metrics_after["avg_betweenness"] - metrics_before["avg_betweenness"])
        / max(metrics_before["avg_betweenness"], 1e-9) * 100,
        1,
    )

    # Compute per-employee SPOF changes for top-risk employees
    from graph.metrics import compute_betweenness
    bw_before_dict = compute_betweenness(G_before)
    bw_after_dict  = compute_betweenness(G_after)

    # Top 10 employees with biggest betweenness increase after the scenario
    spof_changes = []
    for emp_id in G_after.nodes():
        b = float(bw_before_dict.get(emp_id, 0.0))
        a = float(bw_after_dict.get(emp_id, 0.0))
        if a > b:
            spof_changes.append({"employee_id": emp_id, "spof_before": round(b, 4), "spof_after": round(a, 4)})
    spof_changes.sort(key=lambda x: x["spof_after"] - x["spof_before"], reverse=True)
    spof_top10_delta = spof_changes[:10]

    # Enrich with names
    if spof_top10_delta:
        ids = [r["employee_id"] for r in spof_top10_delta]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, name, department FROM employees WHERE id::text = ANY(%s)",
                (ids,),
            )
            name_map = {r["id"]: {"name": r["name"], "dept": r["department"]} for r in cur.fetchall()}
        for r in spof_top10_delta:
            info = name_map.get(r["employee_id"], {})
            r["name"]       = info.get("name", "")
            r["department"] = info.get("dept", "")

    return {
        "before":           metrics_before,
        "after":            metrics_after,
        "silos_before":     silos_before,
        "silos_after":      silos_after,
        "nodes_removed":    nodes_removed,
        "new_isolated_components": new_components,
        "avg_path_length_delta_pct": path_delta_pct,
        "avg_betweenness_delta_pct": spof_delta,
        "spof_top10_delta": spof_top10_delta,
    }
