"""Unit tests for F3 — Knowledge Risk Quantification.

Tests cover:
  - Pure computation: compute_sole_experts, compute_knowledge_scores_from_contributions,
    merge_with_graph_spof
  - API endpoints: /knowledge/scores, /knowledge/domains,
    /knowledge/employee/{id}, /knowledge/impact/{id}
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from graph.knowledge_risk import (
    compute_knowledge_scores_from_contributions,
    compute_sole_experts,
    merge_with_graph_spof,
)


# ─── Pure computation ─────────────────────────────────────────────────────────


class TestComputeSoleExperts:
    def test_sole_expert_single_contributor(self):
        contributions = {("emp-a", "payments"): 5}
        sole = compute_sole_experts(contributions)
        assert ("emp-a", "payments") in sole

    def test_not_sole_when_two_contributors(self):
        contributions = {
            ("emp-a", "payments"): 5,
            ("emp-b", "payments"): 3,
        }
        sole = compute_sole_experts(contributions)
        assert ("emp-a", "payments") not in sole
        assert ("emp-b", "payments") not in sole

    def test_sole_expert_per_domain_independently(self):
        contributions = {
            ("emp-a", "payments"): 5,   # sole in payments
            ("emp-a", "devops"): 2,     # not sole in devops
            ("emp-b", "devops"): 4,
        }
        sole = compute_sole_experts(contributions)
        assert ("emp-a", "payments") in sole
        assert ("emp-a", "devops") not in sole
        assert ("emp-b", "devops") not in sole

    def test_zero_count_excluded_from_sole(self):
        # emp-b has 0 docs → not really a contributor
        contributions = {
            ("emp-a", "ml"): 3,
            ("emp-b", "ml"): 0,
        }
        sole = compute_sole_experts(contributions)
        assert ("emp-a", "ml") in sole

    def test_empty_contributions_returns_empty(self):
        assert compute_sole_experts({}) == set()

    def test_multiple_domains_multiple_sole_experts(self):
        contributions = {
            ("emp-a", "frontend"): 4,
            ("emp-b", "backend"): 6,
        }
        sole = compute_sole_experts(contributions)
        assert ("emp-a", "frontend") in sole
        assert ("emp-b", "backend") in sole


class TestComputeKnowledgeScores:
    def _base_contributions(self) -> dict[tuple[str, str], int]:
        return {
            ("emp-a", "payments"): 10,
            ("emp-a", "devops"): 5,
            ("emp-b", "devops"): 8,
            ("emp-c", "ml"): 3,
        }

    def test_returns_entry_for_each_employee(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        assert set(results.keys()) == {"emp-a", "emp-b", "emp-c"}

    def test_knowledge_score_in_zero_one(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        for emp_id, data in results.items():
            assert 0.0 <= data["knowledge_score"] <= 1.0, f"{emp_id}: {data['knowledge_score']}"

    def test_sole_expert_count_correct(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        # emp-a is sole in payments, emp-b and emp-a share devops → emp-a sole_count = 1
        assert results["emp-a"]["sole_expert_count"] == 1
        assert results["emp-b"]["sole_expert_count"] == 0
        assert results["emp-c"]["sole_expert_count"] == 1

    def test_domain_count_correct(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        assert results["emp-a"]["domain_count"] == 2
        assert results["emp-b"]["domain_count"] == 1
        assert results["emp-c"]["domain_count"] == 1

    def test_doc_count_correct(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        assert results["emp-a"]["doc_count"] == 15   # 10 + 5
        assert results["emp-b"]["doc_count"] == 8
        assert results["emp-c"]["doc_count"] == 3

    def test_sole_expert_domains_listed(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        assert "payments" in results["emp-a"]["sole_expert_domains"]
        assert results["emp-b"]["sole_expert_domains"] == []

    def test_expertise_per_domain_has_required_keys(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        for domain, detail in results["emp-a"]["expertise_per_domain"].items():
            assert "doc_count" in detail
            assert "is_sole_expert" in detail
            assert "expertise_score" in detail

    def test_expertise_score_in_zero_one(self):
        results = compute_knowledge_scores_from_contributions(self._base_contributions())
        for emp_id, data in results.items():
            for domain, detail in data["expertise_per_domain"].items():
                assert 0.0 <= detail["expertise_score"] <= 1.0

    def test_sole_expert_gets_expertise_bonus(self):
        contributions = {
            ("emp-a", "ml"): 5,   # sole — gets 1.2x
            ("emp-b", "ml"): 0,   # zero → excluded
        }
        results = compute_knowledge_scores_from_contributions(contributions)
        # emp-a's expertise_score should be capped at 1.0 (1.0 × 1.2 capped)
        assert results["emp-a"]["expertise_per_domain"]["ml"]["expertise_score"] == pytest.approx(1.0)

    def test_empty_contributions_returns_empty(self):
        assert compute_knowledge_scores_from_contributions({}) == {}

    def test_all_zero_counts_returns_empty(self):
        contributions = {("emp-a", "ml"): 0, ("emp-b", "ml"): 0}
        assert compute_knowledge_scores_from_contributions(contributions) == {}

    def test_knowledge_score_rounded_to_4_decimals(self):
        contributions = {("emp-a", "x"): 1}
        results = compute_knowledge_scores_from_contributions(contributions)
        score = results["emp-a"]["knowledge_score"]
        assert score == round(score, 4)


class TestMergeWithGraphSpof:
    def test_employee_in_both(self):
        k_scores = {"emp-a": {"knowledge_score": 0.8}}
        graph_spof = {"emp-a": 0.6}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=0.3)
        expected = round((1 - 0.3) * 0.6 + 0.3 * 0.8, 4)
        assert merged["emp-a"] == pytest.approx(expected)

    def test_employee_only_in_graph_spof(self):
        k_scores: dict = {}
        graph_spof = {"emp-b": 0.5}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=0.3)
        expected = round(0.7 * 0.5 + 0.3 * 0.0, 4)
        assert merged["emp-b"] == pytest.approx(expected)

    def test_employee_only_in_knowledge_scores(self):
        k_scores = {"emp-c": {"knowledge_score": 0.9}}
        graph_spof: dict = {}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=0.3)
        expected = round(0.7 * 0.0 + 0.3 * 0.9, 4)
        assert merged["emp-c"] == pytest.approx(expected)

    def test_delta_zero_equals_pure_graph(self):
        k_scores = {"emp-a": {"knowledge_score": 1.0}}
        graph_spof = {"emp-a": 0.4}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=0.0)
        assert merged["emp-a"] == pytest.approx(0.4)

    def test_delta_one_equals_pure_knowledge(self):
        k_scores = {"emp-a": {"knowledge_score": 0.7}}
        graph_spof = {"emp-a": 0.9}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=1.0)
        assert merged["emp-a"] == pytest.approx(0.7)

    def test_union_of_all_employees(self):
        k_scores = {"emp-a": {"knowledge_score": 0.5}}
        graph_spof = {"emp-b": 0.6}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=0.3)
        assert "emp-a" in merged
        assert "emp-b" in merged

    def test_rounded_to_4_decimals(self):
        k_scores = {"emp-x": {"knowledge_score": 1 / 3}}
        graph_spof = {"emp-x": 2 / 3}
        merged = merge_with_graph_spof(k_scores, graph_spof, delta_k=0.3)
        v = merged["emp-x"]
        assert v == round(v, 4)


# ─── API endpoints ────────────────────────────────────────────────────────────

_COMPUTED_AT = date(2025, 5, 1)

_KNOWLEDGE_SCORE_ROW: dict[str, Any] = {
    "employee_id": "aaaa-1111",
    "name": "Alice",
    "department": "Engineering",
    "knowledge_score": 0.75,
    "sole_expert_count": 2,
    "domain_count": 3,
    "doc_count": 20,
    "enhanced_spof_score": 0.62,
    "impacted_departments": ["Sales", "HR"],
    "computed_at": _COMPUTED_AT,
}

_DOMAIN_ROW: dict[str, Any] = {
    "domain": "payments",
    "total_docs": 30,
    "contributor_count": 1,
    "sole_expert_id": "aaaa-1111",
    "sole_expert_name": "Alice",
}

_PROFILE_ROW: dict[str, Any] = {
    **_KNOWLEDGE_SCORE_ROW,
    "domains": [
        {
            "domain": "payments",
            "doc_count": 15,
            "is_sole_expert": True,
            "expertise_score": 0.9,
        }
    ],
}


@pytest.fixture()
def client():
    from api.main import app
    from api.deps import get_db
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


class TestKnowledgeScoresEndpoint:
    def test_returns_200_with_scores(self, client):
        with (
            patch("api.routers.knowledge.queries.fetch_latest_knowledge_date", return_value=_COMPUTED_AT),
            patch("api.routers.knowledge.queries.fetch_knowledge_scores", return_value=[_KNOWLEDGE_SCORE_ROW]),
        ):
            resp = client.get("/knowledge/scores")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["scores"][0]["employee_id"] == "aaaa-1111"
        assert body["scores"][0]["knowledge_score"] == 0.75
        assert body["scores"][0]["impacted_departments"] == ["Sales", "HR"]

    def test_returns_404_when_no_runs(self, client):
        with patch("api.routers.knowledge.queries.fetch_latest_knowledge_date", return_value=None):
            resp = client.get("/knowledge/scores")
        assert resp.status_code == 404

    def test_min_score_forwarded(self, client):
        with (
            patch("api.routers.knowledge.queries.fetch_latest_knowledge_date", return_value=_COMPUTED_AT),
            patch("api.routers.knowledge.queries.fetch_knowledge_scores", return_value=[]) as mock_fetch,
        ):
            client.get("/knowledge/scores?min_score=0.5")
        call_args = mock_fetch.call_args
        assert call_args.args[2] == 0.5 or call_args[0][2] == 0.5

    def test_returns_404_when_date_has_no_data(self, client):
        with (
            patch("api.routers.knowledge.queries.fetch_latest_knowledge_date", return_value=_COMPUTED_AT),
            patch("api.routers.knowledge.queries.fetch_knowledge_scores", return_value=[]),
        ):
            resp = client.get(f"/knowledge/scores?date={_COMPUTED_AT}")
        assert resp.status_code == 404


class TestKnowledgeDomainsEndpoint:
    def test_returns_200_with_domains(self, client):
        with patch("api.routers.knowledge.queries.fetch_knowledge_domains", return_value=[_DOMAIN_ROW]):
            resp = client.get("/knowledge/domains")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["domains"][0]["domain"] == "payments"
        assert body["domains"][0]["sole_expert_name"] == "Alice"

    def test_returns_200_empty_when_no_domains(self, client):
        with patch("api.routers.knowledge.queries.fetch_knowledge_domains", return_value=[]):
            resp = client.get("/knowledge/domains")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_domain_without_sole_expert(self, client):
        row = {**_DOMAIN_ROW, "contributor_count": 2, "sole_expert_id": None, "sole_expert_name": None}
        with patch("api.routers.knowledge.queries.fetch_knowledge_domains", return_value=[row]):
            resp = client.get("/knowledge/domains")
        body = resp.json()
        assert body["domains"][0]["sole_expert_id"] is None


class TestEmployeeKnowledgeProfileEndpoint:
    def test_returns_200_with_profile(self, client):
        with patch(
            "api.routers.knowledge.queries.fetch_employee_knowledge_profile",
            return_value=_PROFILE_ROW,
        ):
            resp = client.get("/knowledge/employee/aaaa-1111")
        assert resp.status_code == 200
        body = resp.json()
        assert body["employee_id"] == "aaaa-1111"
        assert len(body["domains"]) == 1
        assert body["domains"][0]["is_sole_expert"] is True

    def test_returns_404_when_no_record(self, client):
        with patch(
            "api.routers.knowledge.queries.fetch_employee_knowledge_profile",
            return_value=None,
        ):
            resp = client.get("/knowledge/employee/not-found")
        assert resp.status_code == 404


class TestKnowledgeImpactEndpoint:
    _IMPACT = {
        "employee_id": "aaaa-1111",
        "name": "Alice",
        "department": "Engineering",
        "sole_expert_count": 2,
        "domain_count": 3,
        "knowledge_score": 0.75,
        "enhanced_spof_score": 0.62,
        "sole_expert_domains": ["payments", "devops"],
        "impacted_departments": ["Sales", "HR"],
        "statement": "If Alice leaves, 2 departments lose their only expert in: payments, devops.",
        "computed_at": "2025-05-01",
    }

    def test_returns_200_with_statement(self, client):
        with patch(
            "graph.knowledge_risk.get_impact_statement",
            return_value=self._IMPACT,
        ):
            resp = client.get("/knowledge/impact/aaaa-1111")
        assert resp.status_code == 200
        body = resp.json()
        assert "Alice" in body["statement"]
        assert body["sole_expert_domains"] == ["payments", "devops"]

    def test_returns_404_when_no_record(self, client):
        with patch(
            "graph.knowledge_risk.get_impact_statement",
            return_value={},
        ):
            resp = client.get("/knowledge/impact/not-found")
        assert resp.status_code == 404
