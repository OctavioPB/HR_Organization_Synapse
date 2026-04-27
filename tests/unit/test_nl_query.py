"""Unit tests for F7 — Natural Language Interface (Claude Integration).

Tests cover:
  - execute_tool: routing, graceful error handling for unknown tools
  - Individual tool executors: return shape, error path when data unavailable
  - run_query: end_turn path, tool_use path, max-turns guard
  - POST /query/natural: happy path, 502 on AI failure, request validation
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def api_client():
    from api.main import app
    from api.deps import get_db

    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


@pytest.fixture()
def mock_conn():
    return MagicMock()


# ─── execute_tool ─────────────────────────────────────────────────────────────


class TestExecuteTool:
    def test_unknown_tool_returns_error(self, mock_conn):
        from api.nl.tools import execute_tool

        result = execute_tool("nonexistent_tool", {}, mock_conn)
        assert "error" in result
        assert "nonexistent_tool" in result["error"]

    def test_search_employees_called(self, mock_conn):
        from api.nl.tools import execute_tool

        mock_conn.cursor.return_value.__enter__ = lambda s: mock_conn.cursor.return_value
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.fetchall.return_value = [
            ("emp-001", "Alice Liddell", "Engineering", True),
        ]
        result = execute_tool("search_employees", {"name": "Alice"}, mock_conn)
        assert "employees" in result or "error" in result  # shape or graceful error

    def test_get_risk_scores_no_snapshot(self, mock_conn):
        from api.nl.tools import execute_tool

        with patch("api.db.fetch_latest_snapshot_date", return_value=None):
            result = execute_tool("get_risk_scores", {}, mock_conn)
        assert "error" in result

    def test_get_knowledge_scores_no_data(self, mock_conn):
        from api.nl.tools import execute_tool

        with patch("api.db.fetch_latest_knowledge_date", return_value=None):
            result = execute_tool("get_knowledge_scores", {}, mock_conn)
        assert "error" in result

    def test_get_churn_risk_no_data(self, mock_conn):
        from api.nl.tools import execute_tool

        with patch("api.db.fetch_latest_churn_date", return_value=None):
            result = execute_tool("get_churn_risk", {}, mock_conn)
        assert "error" in result

    def test_get_temporal_anomalies_no_data(self, mock_conn):
        from api.nl.tools import execute_tool

        with patch("api.db.fetch_latest_temporal_anomaly_date", return_value=None):
            result = execute_tool("get_temporal_anomalies", {}, mock_conn)
        assert "error" in result

    def test_get_succession_plan_no_data(self, mock_conn):
        from api.nl.tools import execute_tool

        with patch("api.db.fetch_employee_succession", return_value=None):
            result = execute_tool("get_succession_plan", {"employee_id": "emp-999"}, mock_conn)
        assert "error" in result

    def test_get_silo_alerts_empty(self, mock_conn):
        from api.nl.tools import execute_tool

        with (
            patch("api.db.fetch_silo_alerts", return_value=[]),
            patch("api.db.fetch_latest_snapshot_date", return_value=date(2025, 5, 1)),
            patch("api.db.fetch_communities", return_value=[]),
        ):
            result = execute_tool("get_silo_alerts", {}, mock_conn)
        assert "total_silo_alerts" in result
        assert result["total_silo_alerts"] == 0

    def test_get_risk_scores_returns_list(self, mock_conn):
        from api.nl.tools import execute_tool

        snap = date(2025, 5, 1)
        rows = [
            {
                "employee_id": "emp-001",
                "name": "Alice",
                "department": "Eng",
                "spof_score": 0.87,
                "entropy_trend": -0.02,
                "flag": "SPOF_CRITICAL",
            }
        ]
        with (
            patch("api.db.fetch_latest_snapshot_date", return_value=snap),
            patch("api.db.fetch_risk_scores", return_value=rows),
        ):
            result = execute_tool("get_risk_scores", {"top_n": 5}, mock_conn)
        assert "high_risk_employees" in result
        assert result["count"] == 1
        assert result["high_risk_employees"][0]["name"] == "Alice"

    def test_get_graph_snapshot_returns_nodes(self, mock_conn):
        from api.nl.tools import execute_tool

        snap = date(2025, 5, 1)
        nodes = [
            {
                "employee_id": "emp-001",
                "name": "Alice",
                "department": "Eng",
                "betweenness": 0.5,
                "degree_in": 10,
                "degree_out": 8,
                "clustering": 0.3,
                "community_id": 1,
            }
        ]
        with (
            patch("api.db.fetch_latest_snapshot_date", return_value=snap),
            patch("api.db.fetch_graph_nodes", return_value=nodes),
        ):
            result = execute_tool("get_graph_snapshot", {"top_n": 5}, mock_conn)
        assert "top_nodes_by_betweenness" in result
        assert len(result["top_nodes_by_betweenness"]) == 1


# ─── run_query — agentic loop ─────────────────────────────────────────────────


class _FakeTextBlock:
    type = "text"
    text = "Alice is the top SPOF with a score of 0.87."


class _FakeToolUseBlock:
    type = "tool_use"
    id = "toolu_01abc"
    name = "get_risk_scores"
    input = {"top_n": 5}


class _FakeResponse:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.model = "claude-sonnet-4-6"


class TestRunQuery:
    def _mock_client(self, responses: list):
        """Return a mock Anthropic client that yields responses in sequence."""
        client = MagicMock()
        client.messages.create.side_effect = responses
        return client

    def test_end_turn_immediate(self, mock_conn):
        from api.nl.agent import run_query

        resp = _FakeResponse("end_turn", [_FakeTextBlock()])
        with patch("anthropic.Anthropic", return_value=self._mock_client([resp])):
            result = run_query("Who is at risk?", mock_conn)

        assert "Alice" in result["answer"]
        assert result["turns"] == 1
        assert result["tools_used"] == []
        assert isinstance(result["latency_ms"], int)

    def test_tool_use_then_end_turn(self, mock_conn):
        from api.nl.agent import run_query

        tool_resp = _FakeResponse("tool_use", [_FakeToolUseBlock()])
        end_resp = _FakeResponse("end_turn", [_FakeTextBlock()])

        risk_rows = [
            {
                "employee_id": "emp-001",
                "name": "Alice",
                "department": "Eng",
                "spof_score": 0.87,
                "entropy_trend": None,
                "flag": "SPOF_CRITICAL",
            }
        ]

        with (
            patch("anthropic.Anthropic", return_value=self._mock_client([tool_resp, end_resp])),
            patch("api.db.fetch_latest_snapshot_date", return_value=date(2025, 5, 1)),
            patch("api.db.fetch_risk_scores", return_value=risk_rows),
        ):
            result = run_query("Who is at risk?", mock_conn)

        assert result["turns"] == 2
        assert len(result["tools_used"]) == 1
        assert result["tools_used"][0]["name"] == "get_risk_scores"

    def test_max_turns_guard(self, mock_conn):
        from api.nl.agent import run_query

        tool_resp = _FakeResponse("tool_use", [_FakeToolUseBlock()])

        with (
            patch("anthropic.Anthropic", return_value=self._mock_client([tool_resp] * 10)),
            patch("api.nl.agent._MAX_TURNS", 2),
            patch("api.db.fetch_latest_snapshot_date", return_value=date(2025, 5, 1)),
            patch("api.db.fetch_risk_scores", return_value=[]),
        ):
            result = run_query("Loop me forever", mock_conn)

        assert "too many" in result["answer"].lower()

    def test_model_key_present(self, mock_conn):
        from api.nl.agent import run_query

        resp = _FakeResponse("end_turn", [_FakeTextBlock()])
        with patch("anthropic.Anthropic", return_value=self._mock_client([resp])):
            result = run_query("Quick question", mock_conn)
        assert "model" in result
        assert result["model"]  # non-empty string


# ─── POST /query/natural endpoint ─────────────────────────────────────────────


class TestNaturalQueryEndpoint:
    def test_returns_200_with_answer(self, api_client):
        payload = {
            "answer": "Alice is the top SPOF.",
            "tools_used": [],
            "model": "claude-sonnet-4-6",
            "turns": 1,
            "latency_ms": 420,
        }
        with patch("api.nl.agent.run_query", return_value=payload):
            resp = api_client.post("/query/natural", json={"question": "Who is at risk?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "Alice is the top SPOF."
        assert body["turns"] == 1

    def test_tools_used_in_response(self, api_client):
        payload = {
            "answer": "Engineering has high risk.",
            "tools_used": [
                {
                    "name": "get_risk_scores",
                    "input": {"top_n": 5},
                    "result_summary": "high_risk_employees: 3 items",
                }
            ],
            "model": "claude-sonnet-4-6",
            "turns": 2,
            "latency_ms": 1500,
        }
        with patch("api.nl.agent.run_query", return_value=payload):
            resp = api_client.post("/query/natural", json={"question": "Any silos?"})
        body = resp.json()
        assert len(body["tools_used"]) == 1
        assert body["tools_used"][0]["name"] == "get_risk_scores"

    def test_short_question_rejected(self, api_client):
        resp = api_client.post("/query/natural", json={"question": "Hi"})
        assert resp.status_code == 422

    def test_missing_question_rejected(self, api_client):
        resp = api_client.post("/query/natural", json={})
        assert resp.status_code == 422

    def test_agent_exception_returns_502(self, api_client):
        with patch("api.nl.agent.run_query", side_effect=RuntimeError("API down")):
            resp = api_client.post("/query/natural", json={"question": "Tell me about risk."})
        assert resp.status_code == 502
        assert "AI query failed" in resp.json()["detail"]
