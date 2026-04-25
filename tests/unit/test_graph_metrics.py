"""Unit tests for graph analytics — no database or Kafka required.

Test topologies used:
  - Star (bidirectional):     center ↔ N leaves
  - Clique (complete):        all pairs connected bidirectionally
  - Bridge:                   two cliques connected by a single bidirectional bridge
  - Isolated cliques:         two cliques with NO cross-community edges (silo scenario)
  - Path:                     linear chain A → B → C → D
"""

import networkx as nx
import pytest

from graph.metrics import (
    compute_betweenness,
    compute_clustering,
    compute_community,
    compute_cross_dept_ratio,
    compute_degree_centrality,
)
from graph.risk_scorer import compute_spof_score, score_all
from graph.silo_detector import SiloAlert, detect_silos


# ─── Graph fixtures ───────────────────────────────────────────────────────────


def _bidi_star(n_leaves: int) -> nx.DiGraph:
    """Directed star graph with bidirectional edges: center ↔ each leaf."""
    G = nx.DiGraph()
    for i in range(n_leaves):
        leaf = f"L{i}"
        G.add_edge("center", leaf, weight=1.0)
        G.add_edge(leaf, "center", weight=1.0)
    return G


def _bidi_clique(n: int, prefix: str = "") -> nx.DiGraph:
    """Directed complete graph with bidirectional edges."""
    G = nx.DiGraph()
    nodes = [f"{prefix}N{i}" for i in range(n)]
    for u in nodes:
        for v in nodes:
            if u != v:
                G.add_edge(u, v, weight=1.0)
    return G


def _bridge_graph() -> tuple[nx.DiGraph, str, str]:
    """Two 4-node cliques connected by a single bidirectional bridge A0 ↔ B0.

    Returns (G, bridge_node_A, bridge_node_B).
    """
    G = nx.DiGraph()
    a_nodes = [f"A{i}" for i in range(4)]
    b_nodes = [f"B{i}" for i in range(4)]

    for group in (a_nodes, b_nodes):
        for u in group:
            for v in group:
                if u != v:
                    G.add_edge(u, v, weight=1.0)

    G.add_edge("A0", "B0", weight=1.0)
    G.add_edge("B0", "A0", weight=1.0)
    return G, "A0", "B0"


def _isolated_cliques_with_depts() -> nx.DiGraph:
    """Two 3-node cliques, no edges between them, different departments."""
    G = nx.DiGraph()
    for u, v in [("A1", "A2"), ("A2", "A3"), ("A1", "A3")]:
        G.add_edge(u, v, weight=1.0)
        G.add_edge(v, u, weight=1.0)
    for u, v in [("B1", "B2"), ("B2", "B3"), ("B1", "B3")]:
        G.add_edge(u, v, weight=1.0)
        G.add_edge(v, u, weight=1.0)
    for n in ["A1", "A2", "A3"]:
        G.nodes[n]["department"] = "Engineering"
    for n in ["B1", "B2", "B3"]:
        G.nodes[n]["department"] = "Sales"
    return G


# ─── compute_betweenness ──────────────────────────────────────────────────────


def test_betweenness_star_center_is_one():
    """In a bidirectional star, the center is on every leaf-to-leaf path → betweenness = 1.0."""
    G = _bidi_star(n_leaves=5)
    b = compute_betweenness(G)
    assert abs(b["center"] - 1.0) < 1e-6, f"Expected 1.0, got {b['center']}"


def test_betweenness_star_leaves_are_zero():
    G = _bidi_star(n_leaves=5)
    b = compute_betweenness(G)
    for i in range(5):
        assert abs(b[f"L{i}"]) < 1e-6, f"Leaf L{i} betweenness should be 0"


def test_betweenness_clique_all_zero():
    """In a complete graph, no node lies on more shortest paths than others → all 0."""
    G = _bidi_clique(5)
    b = compute_betweenness(G)
    assert all(abs(v) < 1e-6 for v in b.values()), f"Clique betweenness not zero: {b}"


def test_betweenness_bridge_nodes_rank_highest():
    """Bridge nodes (A0, B0) must have the highest betweenness in a two-clique bridge graph."""
    G, bridge_a, bridge_b = _bridge_graph()
    b = compute_betweenness(G)
    max_node = max(b, key=b.get)
    assert max_node in {bridge_a, bridge_b}, (
        f"Expected bridge node {bridge_a} or {bridge_b} to have max betweenness, "
        f"got {max_node} ({b[max_node]:.4f})"
    )


def test_betweenness_bridge_nodes_above_others():
    """Both bridge nodes must exceed every non-bridge node in betweenness."""
    G, bridge_a, bridge_b = _bridge_graph()
    b = compute_betweenness(G)
    non_bridge_max = max(v for n, v in b.items() if n not in {bridge_a, bridge_b})
    assert b[bridge_a] > non_bridge_max
    assert b[bridge_b] > non_bridge_max


def test_betweenness_tiny_graph_returns_zeros():
    """Graphs with < 3 nodes cannot have non-trivial betweenness."""
    G = nx.DiGraph()
    G.add_edge("A", "B", weight=1.0)
    b = compute_betweenness(G)
    assert all(v == 0.0 for v in b.values())


# ─── compute_degree_centrality ────────────────────────────────────────────────


def test_degree_centrality_star_center_max():
    G = _bidi_star(n_leaves=4)
    deg_in, deg_out = compute_degree_centrality(G)
    assert deg_in["center"] == max(deg_in.values())
    assert deg_out["center"] == max(deg_out.values())


def test_degree_centrality_clique_uniform():
    """All nodes in a complete graph have equal degree centrality."""
    G = _bidi_clique(4)
    deg_in, deg_out = compute_degree_centrality(G)
    in_values = list(deg_in.values())
    assert max(in_values) - min(in_values) < 1e-6


def test_degree_centrality_all_in_01():
    G = _bidi_star(n_leaves=5)
    deg_in, deg_out = compute_degree_centrality(G)
    for v in list(deg_in.values()) + list(deg_out.values()):
        assert 0.0 <= v <= 1.0, f"Degree centrality {v} out of [0, 1]"


# ─── compute_clustering ───────────────────────────────────────────────────────


def test_clustering_clique_is_one():
    """In a complete graph, every node's neighbours are all connected → clustering = 1.0."""
    G = _bidi_clique(5)
    c = compute_clustering(G)
    assert all(abs(v - 1.0) < 1e-6 for v in c.values()), f"Clique clustering not 1.0: {c}"


def test_clustering_star_leaves_are_zero():
    """Leaf nodes in a star have only the center as neighbour — no triangles → clustering = 0."""
    G = _bidi_star(n_leaves=5)
    c = compute_clustering(G)
    for i in range(5):
        assert abs(c[f"L{i}"]) < 1e-6, f"Star leaf L{i} clustering should be 0"


def test_clustering_all_in_01():
    G, _, _ = _bridge_graph()
    c = compute_clustering(G)
    for v in c.values():
        assert 0.0 <= v <= 1.0, f"Clustering coefficient {v} out of [0, 1]"


# ─── compute_community ────────────────────────────────────────────────────────


def test_community_isolated_cliques_two_groups():
    """Two disconnected cliques must be assigned to two different communities."""
    G = _isolated_cliques_with_depts()
    communities = compute_community(G, random_state=0)
    a_comm = {communities[n] for n in ["A1", "A2", "A3"]}
    b_comm = {communities[n] for n in ["B1", "B2", "B3"]}
    assert len(a_comm) == 1, "All A nodes must be in the same community"
    assert len(b_comm) == 1, "All B nodes must be in the same community"
    assert a_comm != b_comm, "A and B cliques must be in different communities"


def test_community_returns_int_values():
    G = _bidi_star(n_leaves=4)
    communities = compute_community(G, random_state=0)
    assert all(isinstance(v, int) for v in communities.values())


def test_community_covers_all_nodes():
    G, _, _ = _bridge_graph()
    communities = compute_community(G, random_state=0)
    assert set(communities.keys()) == set(G.nodes())


# ─── compute_cross_dept_ratio ─────────────────────────────────────────────────


def test_cross_dept_ratio_all_cross():
    """All edges cross departments → every node should have ratio = 1.0."""
    G = nx.DiGraph()
    G.add_edge("A", "B", weight=1.0)
    G.nodes["A"]["department"] = "Engineering"
    G.nodes["B"]["department"] = "Sales"
    ratio = compute_cross_dept_ratio(G)
    assert abs(ratio["A"] - 1.0) < 1e-6


def test_cross_dept_ratio_no_cross():
    """All edges within same department → ratio = 0.0."""
    G = _bidi_clique(4, prefix="")
    for n in G.nodes():
        G.nodes[n]["department"] = "Engineering"
    ratio = compute_cross_dept_ratio(G)
    assert all(abs(v) < 1e-6 for v in ratio.values())


def test_cross_dept_ratio_isolated_nodes_zero():
    """Nodes with no out-edges must get ratio 0.0 (not division error)."""
    G = nx.DiGraph()
    G.add_node("lone", department="HR")
    ratio = compute_cross_dept_ratio(G)
    assert ratio["lone"] == 0.0


def test_cross_dept_ratio_mixed():
    """Node with 2 cross-dept and 1 same-dept out-edge should be 2/3."""
    G = nx.DiGraph()
    G.add_edge("X", "A", weight=1.0)
    G.add_edge("X", "B", weight=1.0)
    G.add_edge("X", "C", weight=1.0)
    G.nodes["X"]["department"] = "Engineering"
    G.nodes["A"]["department"] = "Sales"
    G.nodes["B"]["department"] = "HR"
    G.nodes["C"]["department"] = "Engineering"
    ratio = compute_cross_dept_ratio(G)
    assert abs(ratio["X"] - 2 / 3) < 1e-6


# ─── detect_silos ─────────────────────────────────────────────────────────────


def test_silo_detector_fires_for_both_isolated_cliques():
    """Two isolated cliques with no bridges must each trigger a silo alert."""
    G = _isolated_cliques_with_depts()
    communities = compute_community(G, random_state=0)
    alerts = detect_silos(G, communities, threshold=1.0)
    assert len(alerts) == 2, f"Expected 2 silo alerts, got {len(alerts)}"


def test_silo_detector_returns_silo_alert_instances():
    G = _isolated_cliques_with_depts()
    communities = compute_community(G, random_state=0)
    alerts = detect_silos(G, communities, threshold=1.0)
    assert all(isinstance(a, SiloAlert) for a in alerts)


def test_silo_detector_no_alerts_for_fully_connected():
    """A single connected graph with well-distributed edges should not be a silo."""
    G, _, _ = _bridge_graph()
    communities = compute_community(G, random_state=0)
    # Very high threshold — should produce no alerts
    alerts = detect_silos(G, communities, threshold=1000.0)
    assert len(alerts) == 0


def test_silo_detector_sorted_by_ratio_desc():
    G = _isolated_cliques_with_depts()
    communities = compute_community(G, random_state=0)
    alerts = detect_silos(G, communities, threshold=1.0)
    ratios = [a.isolation_ratio for a in alerts]
    assert ratios == sorted(ratios, reverse=True)


def test_silo_detector_severity_critical_at_2x_threshold():
    """isolation_ratio > 2× threshold must produce severity='critical'."""
    G = _isolated_cliques_with_depts()
    communities = compute_community(G, random_state=0)
    # With threshold=1.0 and no external edges, ratio = internal / 1 = 6 > 2×1.0
    alerts = detect_silos(G, communities, threshold=1.0)
    assert any(a.severity == "critical" for a in alerts)


# ─── compute_spof_score ───────────────────────────────────────────────────────


def test_spof_all_zero_inputs():
    score = compute_spof_score(0.0, 0.0, 1.0, 0.0, 0.4, 0.3, 0.2, 0.1)
    assert score == 0.0


def test_spof_betweenness_only():
    score = compute_spof_score(1.0, 0.0, 1.0, 0.0, 0.4, 0.3, 0.2, 0.1)
    assert abs(score - 0.4) < 1e-9


def test_spof_cross_dept_only():
    score = compute_spof_score(0.0, 1.0, 1.0, 0.0, 0.4, 0.3, 0.2, 0.1)
    assert abs(score - 0.3) < 1e-9


def test_spof_low_clustering_increases_score():
    high_cluster = compute_spof_score(0.0, 0.0, 1.0, 0.0, 0.4, 0.3, 0.2, 0.1)
    low_cluster = compute_spof_score(0.0, 0.0, 0.0, 0.0, 0.4, 0.3, 0.2, 0.1)
    assert low_cluster > high_cluster


def test_spof_negative_entropy_trend_increases_score():
    """Negative entropy_trend (withdrawing) must INCREASE SPOF via the -δ×trend term."""
    stable = compute_spof_score(0.5, 0.5, 0.5, 0.0, 0.4, 0.3, 0.2, 0.1)
    withdrawing = compute_spof_score(0.5, 0.5, 0.5, -0.5, 0.4, 0.3, 0.2, 0.1)
    assert withdrawing > stable


def test_spof_all_max_inputs():
    """betweenness=1, cross_dept=1, clustering=0, trend=0 → max score = α+β+γ."""
    score = compute_spof_score(1.0, 1.0, 0.0, 0.0, 0.4, 0.3, 0.2, 0.1)
    expected = 0.4 + 0.3 + 0.2
    assert abs(score - expected) < 1e-9


def test_score_all_star_graph():
    """Center of a star must have the highest SPOF score."""
    G = _bidi_star(n_leaves=6)
    for n in G.nodes():
        G.nodes[n]["department"] = "A" if n == "center" else "B"

    betweenness = compute_betweenness(G)
    clustering = compute_clustering(G)
    scores = score_all(G, betweenness, clustering)

    assert max(scores, key=scores.get) == "center", (
        f"Center should have highest SPOF; got {sorted(scores.items(), key=lambda x: -x[1])[:3]}"
    )
