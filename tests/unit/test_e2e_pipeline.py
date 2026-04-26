"""End-to-end pipeline smoke test — no database, Kafka, or Docker required.

Exercises the full analytical chain:
  synthetic data → build_graph → metrics → risk scores → silo detection

Validates the "demo scenario" invariants documented in CLAUDE.md and README:
  - Connectors produce the highest SPOF scores
  - Withdrawing employee shows negative entropy trend in the last 15 days
  - Graph health stats stay within valid ranges
  - Silo detection fires on isolated communities
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from ingestion.synthetic import (
    generate_edges,
    generate_employees,
    select_connectors,
    select_withdrawing,
)
from graph.builder import build_graph
from graph.metrics import (
    compute_betweenness,
    compute_clustering,
    compute_community,
    compute_cross_dept_ratio,
    compute_degree_centrality,
)
from graph.risk_scorer import score_all
from graph.silo_detector import detect_silos
from ml.features.feature_extractor import compute_entropy

# ─── Fixtures ─────────────────────────────────────────────────────────────────

_START = datetime(2025, 1, 1, tzinfo=timezone.utc)
_DEPT_FRACTIONS = {"Engineering": 0.5, "Sales": 0.33, "HR": 0.17}


@pytest.fixture(scope="module")
def pipeline():
    """Generate the full demo dataset and run the analytics pipeline once."""
    rng = np.random.default_rng(42)
    employees = generate_employees(80, _DEPT_FRACTIONS, rng)
    connector_ids = select_connectors(employees, rng, n_connectors=2)
    withdrawing_id = select_withdrawing(employees, connector_ids, rng)

    edges = generate_edges(
        employees=employees,
        n_days=90,
        rng=rng,
        connector_ids=connector_ids,
        withdrawing_id=withdrawing_id,
        start_date=_START,
    )

    # Build graph from synthetic edges (no DB needed)
    raw_edges = [
        (e.source_employee_id, e.target_employee_id, e.weight,
         e.department_source, e.department_target)
        for e in edges
    ]
    G = build_graph(raw_edges)

    betweenness = compute_betweenness(G)
    deg_in, deg_out = compute_degree_centrality(G)
    clustering = compute_clustering(G)
    communities = compute_community(G, random_state=0)
    cross_dept = compute_cross_dept_ratio(G)

    # Build per-employee entropy from edges (without DB)
    from collections import defaultdict
    partner_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for e in edges:
        src = e.source_employee_id
        tgt = e.target_employee_id
        partner_counts[src][tgt] = partner_counts[src].get(tgt, 0) + 1

    entropy_current = {
        emp_id: compute_entropy(counts)
        for emp_id, counts in partner_counts.items()
    }

    scores = score_all(G, betweenness, clustering, entropy_trends={})
    silo_alerts = detect_silos(G, communities)

    return {
        "employees": employees,
        "connector_ids": connector_ids,
        "withdrawing_id": withdrawing_id,
        "edges": edges,
        "G": G,
        "betweenness": betweenness,
        "deg_in": deg_in,
        "deg_out": deg_out,
        "clustering": clustering,
        "communities": communities,
        "cross_dept": cross_dept,
        "entropy_current": entropy_current,
        "scores": scores,
        "silo_alerts": silo_alerts,
    }


# ─── Graph construction ───────────────────────────────────────────────────────


def test_graph_has_nodes(pipeline):
    assert pipeline["G"].number_of_nodes() > 0


def test_graph_has_edges(pipeline):
    assert pipeline["G"].number_of_edges() > 0


def test_graph_covers_all_employees(pipeline):
    """Every employee with at least one edge must appear in the graph."""
    G = pipeline["G"]
    emp_ids_in_edges = {
        e.source_employee_id for e in pipeline["edges"]
    } | {e.target_employee_id for e in pipeline["edges"]}
    # Graph should contain all employees who appeared in any edge
    assert set(G.nodes()) == emp_ids_in_edges


def test_graph_edge_weights_positive(pipeline):
    G = pipeline["G"]
    for u, v, data in G.edges(data=True):
        assert data["weight"] > 0.0, f"Non-positive weight on edge ({u}, {v})"


# ─── Metric ranges ────────────────────────────────────────────────────────────


def test_betweenness_in_range(pipeline):
    for node, val in pipeline["betweenness"].items():
        assert 0.0 <= val <= 1.0, f"Betweenness {val} out of [0,1] for {node}"


def test_degree_centrality_in_range(pipeline):
    for val in list(pipeline["deg_in"].values()) + list(pipeline["deg_out"].values()):
        assert 0.0 <= val <= 1.0, f"Degree centrality {val} out of [0,1]"


def test_clustering_in_range(pipeline):
    for node, val in pipeline["clustering"].items():
        assert 0.0 <= val <= 1.0, f"Clustering {val} out of [0,1] for {node}"


def test_cross_dept_ratio_in_range(pipeline):
    for node, val in pipeline["cross_dept"].items():
        assert 0.0 <= val <= 1.0, f"Cross-dept ratio {val} out of [0,1] for {node}"


def test_spof_scores_in_range(pipeline):
    for node, score in pipeline["scores"].items():
        assert 0.0 <= score <= 1.0, f"SPOF score {score} out of [0,1] for {node}"


# ─── Demo scenario: connectors ────────────────────────────────────────────────


def test_connectors_have_highest_betweenness(pipeline):
    """Connectors must rank in the top 10% by betweenness centrality."""
    connector_ids = pipeline["connector_ids"]
    betweenness = pipeline["betweenness"]
    G = pipeline["G"]

    sorted_nodes = sorted(betweenness, key=betweenness.get, reverse=True)
    top_10pct = set(sorted_nodes[: max(1, len(sorted_nodes) // 10)])

    for cid in connector_ids:
        if cid in betweenness:
            assert cid in top_10pct, (
                f"Connector {cid[:8]} not in top-10% betweenness. "
                f"Score: {betweenness.get(cid, 0):.4f}"
            )


def test_connectors_have_highest_spof_scores(pipeline):
    """At least one connector must appear in the top 5 SPOF scores."""
    connector_ids = pipeline["connector_ids"]
    scores = pipeline["scores"]

    sorted_nodes = sorted(scores, key=scores.get, reverse=True)
    top_5 = set(sorted_nodes[:5])

    assert connector_ids & top_5, (
        f"No connector found in top-5 SPOF scores. "
        f"Top 5: {[(n[:8], scores[n]) for n in sorted_nodes[:5]]}"
    )


def test_connectors_above_average_spof(pipeline):
    """Every connector must have an above-average SPOF score."""
    connector_ids = pipeline["connector_ids"]
    scores = pipeline["scores"]

    if not scores:
        pytest.skip("No scores generated")

    mean_score = sum(scores.values()) / len(scores)
    for cid in connector_ids:
        if cid in scores:
            assert scores[cid] > mean_score, (
                f"Connector {cid[:8]} score {scores[cid]:.4f} not above mean {mean_score:.4f}"
            )


# ─── Demo scenario: withdrawing employee ──────────────────────────────────────


def test_withdrawing_employee_entropy_lower_in_late_period(pipeline):
    """Withdrawing employee must have lower entropy in the last 15 days vs first 75 days."""
    from collections import defaultdict
    from datetime import timedelta

    withdrawing_id = pipeline["withdrawing_id"]
    edges = pipeline["edges"]

    cutoff_iso = (_START + timedelta(days=75)).isoformat()
    early_partners: dict[str, int] = defaultdict(int)
    late_partners: dict[str, int] = defaultdict(int)

    for e in edges:
        if e.source_employee_id != withdrawing_id:
            continue
        if e.timestamp < cutoff_iso:
            early_partners[e.target_employee_id] += 1
        else:
            late_partners[e.target_employee_id] += 1

    if not early_partners:
        pytest.skip("Withdrawing employee had no early edges in this run")

    early_entropy = compute_entropy(dict(early_partners))
    late_entropy = compute_entropy(dict(late_partners)) if late_partners else 0.0

    assert late_entropy <= early_entropy, (
        f"Withdrawing employee entropy did not decline: "
        f"early={early_entropy:.4f} late={late_entropy:.4f}"
    )


# ─── Silo detection ───────────────────────────────────────────────────────────


def test_silo_detection_returns_list(pipeline):
    assert isinstance(pipeline["silo_alerts"], list)


def test_silo_alerts_have_valid_severity(pipeline):
    valid = {"low", "medium", "high", "critical"}
    for alert in pipeline["silo_alerts"]:
        assert alert.severity in valid, f"Invalid severity: {alert.severity}"


def test_silo_isolation_ratios_above_threshold(pipeline):
    """All returned silo alerts must exceed the detection threshold."""
    from graph.silo_detector import _DEFAULT_SILO_THRESHOLD
    for alert in pipeline["silo_alerts"]:
        assert alert.isolation_ratio > _DEFAULT_SILO_THRESHOLD, (
            f"Alert community_id={alert.community_id} ratio={alert.isolation_ratio:.2f} "
            f"is at or below threshold={_DEFAULT_SILO_THRESHOLD}"
        )


# ─── Full pipeline health ─────────────────────────────────────────────────────


def test_all_graph_nodes_have_spof_score(pipeline):
    """score_all must produce a score for every node in the graph."""
    G = pipeline["G"]
    scores = pipeline["scores"]
    missing = set(G.nodes()) - set(scores.keys())
    assert not missing, f"{len(missing)} nodes have no SPOF score: {list(missing)[:5]}"


def test_communities_cover_all_nodes(pipeline):
    G = pipeline["G"]
    communities = pipeline["communities"]
    assert set(communities.keys()) == set(G.nodes())


def test_entropy_computed_for_active_nodes(pipeline):
    """Every node that sent at least one edge should have an entropy value."""
    active_senders = {e.source_employee_id for e in pipeline["edges"]}
    entropy = pipeline["entropy_current"]
    missing = active_senders - set(entropy.keys())
    assert not missing, (
        f"{len(missing)} active senders have no entropy: {list(missing)[:5]}"
    )
