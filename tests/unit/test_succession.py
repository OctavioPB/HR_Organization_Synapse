"""Unit tests for F4 — Succession Planning Recommendations.

Tests cover:
  - Pure computation: compute_structural_overlap, compute_domain_overlap,
    find_border_employees, score_candidates
  - API endpoints: GET /succession/recommendations, GET /succession/employee/{id}
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from graph.succession import (
    compute_domain_overlap,
    compute_structural_overlap,
    find_border_employees,
    score_candidates,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _star_graph(center: str, leaves: list[str]) -> nx.DiGraph:
    """Star graph: center connects to every leaf (bidirectional)."""
    G = nx.DiGraph()
    G.add_node(center)
    for leaf in leaves:
        G.add_edge(center, leaf)
        G.add_edge(leaf, center)
    return G


def _bridge_graph() -> tuple[nx.DiGraph, str]:
    """Two cliques connected by a bridge node.

    Community A: a1, a2, a3 (plus bridge)
    Bridge: b (connects A and B)
    Community B: b1, b2, b3 (plus bridge)
    """
    G = nx.DiGraph()
    for u, v in [("a1", "a2"), ("a2", "a3"), ("a3", "a1")]:
        G.add_edge(u, v); G.add_edge(v, u)
    for u, v in [("b1", "b2"), ("b2", "b3"), ("b3", "b1")]:
        G.add_edge(u, v); G.add_edge(v, u)
    G.add_edge("b", "a1"); G.add_edge("a1", "b")
    G.add_edge("b", "b1"); G.add_edge("b1", "b")
    return G, "b"


# ─── compute_structural_overlap ───────────────────────────────────────────────


class TestComputeStructuralOverlap:
    def test_identical_neighbors(self):
        G = nx.DiGraph()
        G.add_edges_from([("s", "n1"), ("s", "n2"), ("c", "n1"), ("c", "n2")])
        # s and c share both neighbors → Jaccard = 1 (excluding each other)
        result = compute_structural_overlap("s", "c", G)
        assert result == pytest.approx(1.0)

    def test_no_common_neighbors(self):
        G = nx.DiGraph()
        G.add_edges_from([("s", "n1"), ("c", "n2")])
        result = compute_structural_overlap("s", "c", G)
        assert result == pytest.approx(0.0)

    def test_partial_overlap(self):
        G = nx.DiGraph()
        # s: {n1, n2}, c: {n2, n3} → intersection={n2}, union={n1,n2,n3} → 1/3
        G.add_edges_from([("s", "n1"), ("s", "n2"), ("c", "n2"), ("c", "n3")])
        result = compute_structural_overlap("s", "c", G)
        assert result == pytest.approx(1 / 3)

    def test_no_neighbors_returns_zero(self):
        G = nx.DiGraph()
        G.add_node("s")
        G.add_node("c")
        result = compute_structural_overlap("s", "c", G)
        assert result == pytest.approx(0.0)

    def test_result_in_zero_one(self):
        G, bridge = _bridge_graph()
        result = compute_structural_overlap(bridge, "a1", G)
        assert 0.0 <= result <= 1.0

    def test_self_excluded_from_neighbor_set(self):
        # s and c share only each other as neighbors — should be 0 after exclusion
        G = nx.DiGraph()
        G.add_edge("s", "c")
        G.add_edge("c", "s")
        result = compute_structural_overlap("s", "c", G)
        assert result == pytest.approx(0.0)

    def test_undirected_edges_counted(self):
        G = nx.DiGraph()
        # n1 points to s (predecessor), n2 is pointed at by s (successor)
        G.add_edge("n1", "s")
        G.add_edge("s", "n2")
        G.add_edge("n1", "c")
        G.add_edge("c", "n2")
        result = compute_structural_overlap("s", "c", G)
        # neighbors(s) = {n1, n2}, neighbors(c) = {n1, n2} → Jaccard = 1
        assert result == pytest.approx(1.0)


# ─── compute_domain_overlap ───────────────────────────────────────────────────


class TestComputeDomainOverlap:
    def test_full_overlap(self):
        domains = {"payments", "devops", "ml"}
        assert compute_domain_overlap(domains, domains) == pytest.approx(1.0)

    def test_no_overlap(self):
        assert compute_domain_overlap({"payments"}, {"devops"}) == pytest.approx(0.0)

    def test_partial_overlap(self):
        source = {"payments", "devops", "ml"}
        candidate = {"payments", "devops"}
        # 2 shared / 3 source domains = 2/3
        assert compute_domain_overlap(source, candidate) == pytest.approx(2 / 3)

    def test_empty_source_returns_zero(self):
        assert compute_domain_overlap(set(), {"payments"}) == pytest.approx(0.0)

    def test_empty_candidate_returns_zero(self):
        assert compute_domain_overlap({"payments"}, set()) == pytest.approx(0.0)

    def test_candidate_superset_capped_at_one(self):
        source = {"payments"}
        candidate = {"payments", "devops", "ml"}
        # 1 shared / 1 source = 1.0 (candidate's extra domains don't change result)
        assert compute_domain_overlap(source, candidate) == pytest.approx(1.0)


# ─── find_border_employees ────────────────────────────────────────────────────


class TestFindBorderEmployees:
    def _community_map(self, assignments: dict[str, int | None]) -> dict[str, int | None]:
        return assignments

    def test_returns_neighbors_outside_community(self):
        G, bridge = _bridge_graph()
        # bridge is in community 0 with a1, a2, a3
        cmap = {"b": 0, "a1": 0, "a2": 0, "a3": 0, "b1": 1, "b2": 1, "b3": 1}
        border = find_border_employees("b", cmap, G)
        # bridge connects to b1 which is outside community 0
        assert "b1" in border

    def test_excludes_source_from_border(self):
        G, bridge = _bridge_graph()
        cmap = {"b": 0, "a1": 0, "a2": 0, "a3": 0, "b1": 1, "b2": 1, "b3": 1}
        border = find_border_employees("b", cmap, G)
        assert "b" not in border

    def test_excludes_same_community_members(self):
        G, bridge = _bridge_graph()
        cmap = {"b": 0, "a1": 0, "a2": 0, "a3": 0, "b1": 1, "b2": 1, "b3": 1}
        border = find_border_employees("b", cmap, G)
        # a1, a2, a3 are in same community → not border
        assert "a1" not in border
        assert "a2" not in border
        assert "a3" not in border

    def test_no_cross_edges_returns_empty(self):
        G = nx.DiGraph()
        G.add_edges_from([("a", "b"), ("b", "a")])
        cmap = {"a": 0, "b": 0}
        border = find_border_employees("a", cmap, G)
        assert len(border) == 0

    def test_no_community_uses_source_only(self):
        G = nx.DiGraph()
        G.add_edges_from([("s", "n1"), ("n1", "n2")])
        cmap: dict[str, int | None] = {"s": None, "n1": 1, "n2": 1}
        border = find_border_employees("s", cmap, G)
        assert "n1" in border

    def test_source_not_in_graph_returns_empty(self):
        G = nx.DiGraph()
        G.add_node("n1")
        cmap = {"absent": 0, "n1": 1}
        border = find_border_employees("absent", cmap, G)
        assert len(border) == 0


# ─── score_candidates ─────────────────────────────────────────────────────────


class TestScoreCandidates:
    def test_returns_at_most_n_candidates(self):
        G = _star_graph("s", ["c1", "c2", "c3", "c4", "c5"])
        node_metrics = {c: {"clustering": 0.5} for c in ["c1", "c2", "c3", "c4", "c5"]}
        results = score_candidates("s", G, node_metrics, {}, ["c1", "c2", "c3", "c4", "c5"], n=3)
        assert len(results) <= 3

    def test_source_not_in_graph_returns_empty(self):
        G = nx.DiGraph()
        G.add_node("c1")
        results = score_candidates("absent", G, {}, {}, ["c1"])
        assert results == []

    def test_candidate_excluded_if_not_in_graph(self):
        G = nx.DiGraph()
        G.add_node("s")
        results = score_candidates("s", G, {}, {}, ["not-in-graph"])
        assert results == []

    def test_sorted_by_compatibility_desc(self):
        # c1 has many shared neighbors → higher structural overlap
        G = nx.DiGraph()
        shared_neighbors = [f"n{i}" for i in range(5)]
        for n in shared_neighbors:
            G.add_edge("s", n); G.add_edge(n, "s")
            G.add_edge("c1", n); G.add_edge(n, "c1")
        # c2 shares no neighbors
        G.add_edge("s", "x"); G.add_edge("x", "s")
        G.add_edge("c2", "y"); G.add_edge("y", "c2")

        node_metrics = {"c1": {"clustering": 0.3}, "c2": {"clustering": 0.3}}
        results = score_candidates("s", G, node_metrics, {}, ["c1", "c2"])
        assert results[0]["candidate_employee_id"] == "c1"

    def test_compatibility_score_in_zero_one(self):
        G, bridge = _bridge_graph()
        node_metrics = {n: {"clustering": 0.5} for n in G.nodes()}
        candidates = ["b1", "b2", "b3"]
        results = score_candidates(bridge, G, node_metrics, {}, candidates)
        for r in results:
            assert 0.0 <= r["compatibility_score"] <= 1.0

    def test_domain_overlap_contributes_to_score(self):
        G = nx.DiGraph()
        G.add_node("s"); G.add_node("c1"); G.add_node("c2")
        # c1 shares source's domains, c2 does not
        node_metrics = {"c1": {"clustering": 0.0}, "c2": {"clustering": 0.0}}
        knowledge = {
            "s": {"payments", "devops"},
            "c1": {"payments", "devops"},
            "c2": {"ml"},
        }
        results = score_candidates(
            "s", G, node_metrics, knowledge, ["c1", "c2"],
            w_struct=0.0, w_clust=0.0, w_domain=1.0
        )
        c1_result = next(r for r in results if r["candidate_employee_id"] == "c1")
        c2_result = next(r for r in results if r["candidate_employee_id"] == "c2")
        assert c1_result["compatibility_score"] > c2_result["compatibility_score"]

    def test_output_has_required_keys(self):
        G = nx.DiGraph()
        G.add_node("s"); G.add_node("c1")
        G.add_edge("s", "c1")
        results = score_candidates("s", G, {"c1": {"clustering": 0.4}}, {}, ["c1"])
        assert len(results) == 1
        r = results[0]
        for key in ("candidate_employee_id", "structural_overlap", "clustering_score",
                    "domain_overlap", "compatibility_score"):
            assert key in r

    def test_compatibility_score_rounded_to_4_decimals(self):
        G = nx.DiGraph()
        G.add_node("s"); G.add_node("c")
        G.add_edge("s", "n1"); G.add_edge("c", "n1")
        node_metrics = {"c": {"clustering": 1 / 3}}
        results = score_candidates("s", G, node_metrics, {}, ["c"])
        score = results[0]["compatibility_score"]
        assert score == round(score, 4)


# ─── API endpoints ────────────────────────────────────────────────────────────

_COMPUTED_AT = date(2025, 6, 1)

_CANDIDATE_ROW = {
    "candidate_employee_id": "cand-0001",
    "candidate_name": "Bob",
    "candidate_department": "Sales",
    "compatibility_score": 0.72,
    "structural_overlap": 0.60,
    "clustering_score": 0.45,
    "domain_overlap": 0.80,
    "rank": 1,
}

_SUCCESSION_ROW = {
    "source_employee_id": "spof-0001",
    "source_name": "Alice",
    "source_department": "Engineering",
    "spof_score": 0.85,
    "computed_at": _COMPUTED_AT,
    "candidates": [_CANDIDATE_ROW],
}


@pytest.fixture()
def client():
    from api.main import app
    from api.deps import get_db
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


class TestSuccessionRecommendationsEndpoint:
    def test_returns_200_with_data(self, client):
        with (
            patch("api.routers.succession.queries.fetch_latest_succession_date", return_value=_COMPUTED_AT),
            patch("api.routers.succession.queries.fetch_succession_recommendations", return_value=[_SUCCESSION_ROW]),
        ):
            resp = client.get("/succession/recommendations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        rec = body["recommendations"][0]
        assert rec["source_name"] == "Alice"
        assert rec["spof_score"] == pytest.approx(0.85)
        assert len(rec["candidates"]) == 1
        assert rec["candidates"][0]["name"] == "Bob"
        assert rec["candidates"][0]["rank"] == 1

    def test_returns_404_when_no_runs(self, client):
        with patch("api.routers.succession.queries.fetch_latest_succession_date", return_value=None):
            resp = client.get("/succession/recommendations")
        assert resp.status_code == 404

    def test_returns_404_when_date_has_no_data(self, client):
        with (
            patch("api.routers.succession.queries.fetch_latest_succession_date", return_value=_COMPUTED_AT),
            patch("api.routers.succession.queries.fetch_succession_recommendations", return_value=[]),
        ):
            resp = client.get(f"/succession/recommendations?date={_COMPUTED_AT}")
        assert resp.status_code == 404

    def test_min_spof_score_forwarded(self, client):
        with (
            patch("api.routers.succession.queries.fetch_latest_succession_date", return_value=_COMPUTED_AT),
            patch("api.routers.succession.queries.fetch_succession_recommendations", return_value=[]) as mock_q,
        ):
            client.get("/succession/recommendations?min_spof_score=0.6")
        # Third positional arg is min_spof_score
        call_args = mock_q.call_args[0]
        assert call_args[2] == pytest.approx(0.6)

    def test_compatibility_score_in_response(self, client):
        with (
            patch("api.routers.succession.queries.fetch_latest_succession_date", return_value=_COMPUTED_AT),
            patch("api.routers.succession.queries.fetch_succession_recommendations", return_value=[_SUCCESSION_ROW]),
        ):
            resp = client.get("/succession/recommendations")
        candidate = resp.json()["recommendations"][0]["candidates"][0]
        assert candidate["compatibility_score"] == pytest.approx(0.72)
        assert candidate["structural_overlap"] == pytest.approx(0.60)
        assert candidate["domain_overlap"] == pytest.approx(0.80)


class TestEmployeeSuccessionEndpoint:
    def test_returns_200_with_plan(self, client):
        with patch(
            "api.routers.succession.queries.fetch_employee_succession",
            return_value=_SUCCESSION_ROW,
        ):
            resp = client.get("/succession/employee/spof-0001")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_employee_id"] == "spof-0001"
        assert body["source_name"] == "Alice"
        assert len(body["candidates"]) == 1

    def test_returns_404_when_no_plan(self, client):
        with patch(
            "api.routers.succession.queries.fetch_employee_succession",
            return_value=None,
        ):
            resp = client.get("/succession/employee/not-a-spof")
        assert resp.status_code == 404

    def test_empty_candidates_list_is_valid(self, client):
        row = {**_SUCCESSION_ROW, "candidates": []}
        with patch(
            "api.routers.succession.queries.fetch_employee_succession",
            return_value=row,
        ):
            resp = client.get("/succession/employee/spof-0001")
        assert resp.status_code == 200
        assert resp.json()["candidates"] == []
