"""Unit tests for the FastAPI API layer.

Strategy: mock functions in api.db (the thin data-access layer) and the
get_db dependency so no real database or Airflow is required.

All tests use FastAPI's dependency_overrides to inject a no-op connection.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def override_db():
    """Replace the real DB dependency with a do-nothing mock connection."""
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield mock_conn
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=True)


def _emp_id() -> str:
    return str(uuid.uuid4())


# ─── Shared sample data ───────────────────────────────────────────────────────


_SNAPSHOT_DATE = date(2025, 4, 25)
_EMP_A = _emp_id()
_EMP_B = _emp_id()
_EMP_C = _emp_id()

_NODE_ROWS = [
    {
        "employee_id": _EMP_A,
        "name": "Alice",
        "department": "Engineering",
        "betweenness": 0.8,
        "degree_in": 0.5,
        "degree_out": 0.6,
        "clustering": 0.3,
        "community_id": 0,
    },
    {
        "employee_id": _EMP_B,
        "name": "Bob",
        "department": "Sales",
        "betweenness": 0.2,
        "degree_in": 0.2,
        "degree_out": 0.2,
        "clustering": 0.7,
        "community_id": 1,
    },
]

_EDGE_ROWS = [
    {"source": _EMP_A, "target": _EMP_B, "weight": 5.0},
    {"source": _EMP_B, "target": _EMP_A, "weight": 3.0},
]

_RISK_ROWS = [
    {
        "employee_id": _EMP_A,
        "name": "Alice",
        "department": "Engineering",
        "spof_score": 0.82,
        "entropy_trend": -0.15,
        "flag": "critical",
        "scored_at": _SNAPSHOT_DATE,
    },
    {
        "employee_id": _EMP_B,
        "name": "Bob",
        "department": "Sales",
        "spof_score": 0.45,
        "entropy_trend": 0.02,
        "flag": "normal",
        "scored_at": _SNAPSHOT_DATE,
    },
]

_ALERT_ROW = {
    "id": str(uuid.uuid4()),
    "fired_at": datetime(2025, 4, 25, 10, 0, 0, tzinfo=timezone.utc),
    "type": "silo",
    "severity": "high",
    "affected_entities": {"community_id": 1, "member_count": 3},
    "details": "Community 1 isolation_ratio=6.00",
    "resolved": False,
    "resolved_at": None,
}


# ─── Health ───────────────────────────────────────────────────────────────────


def test_root_returns_ok(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_returns_healthy(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ─── GET /graph/snapshot ──────────────────────────────────────────────────────


def test_graph_snapshot_returns_200(client):
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_graph_nodes", return_value=_NODE_ROWS),
        patch("api.routers.graph.queries.fetch_graph_edges", return_value=_EDGE_ROWS),
    ):
        resp = client.get("/graph/snapshot")
    assert resp.status_code == 200


def test_graph_snapshot_node_count(client):
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_graph_nodes", return_value=_NODE_ROWS),
        patch("api.routers.graph.queries.fetch_graph_edges", return_value=_EDGE_ROWS),
    ):
        resp = client.get("/graph/snapshot")
    body = resp.json()
    assert body["node_count"] == 2
    assert body["edge_count"] == 2


def test_graph_snapshot_schema(client):
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_graph_nodes", return_value=_NODE_ROWS),
        patch("api.routers.graph.queries.fetch_graph_edges", return_value=_EDGE_ROWS),
    ):
        resp = client.get(f"/graph/snapshot?date={_SNAPSHOT_DATE}")
    node = resp.json()["nodes"][0]
    for field in ("employee_id", "name", "department", "betweenness", "degree_in", "degree_out"):
        assert field in node, f"Missing field: {field}"


def test_graph_snapshot_404_when_no_data(client):
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_graph_nodes", return_value=[]),
    ):
        resp = client.get("/graph/snapshot")
    assert resp.status_code == 404


def test_graph_snapshot_404_when_no_snapshots_ever(client):
    with patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=None):
        resp = client.get("/graph/snapshot")
    assert resp.status_code == 404


# ─── GET /graph/employee/{id} ─────────────────────────────────────────────────


def test_ego_network_returns_200(client):
    ego = {
        "node": _NODE_ROWS[0],
        "neighbors": [_NODE_ROWS[1]],
        "edges": _EDGE_ROWS,
    }
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_ego_network", return_value=ego),
    ):
        resp = client.get(f"/graph/employee/{_EMP_A}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["employee_id"] == _EMP_A
    assert len(body["neighbors"]) == 1


def test_ego_network_404_when_missing(client):
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_ego_network", return_value={}),
    ):
        resp = client.get(f"/graph/employee/{_EMP_A}")
    assert resp.status_code == 404


# ─── GET /graph/communities ───────────────────────────────────────────────────


def test_communities_returns_200(client):
    comm_rows = [
        {
            "community_id": 0,
            "member_count": 1,
            "members": [_EMP_A],
            "departments": ["Engineering"],
            "is_silo": False,
        },
        {
            "community_id": 1,
            "member_count": 1,
            "members": [_EMP_B],
            "departments": ["Sales"],
            "is_silo": True,
        },
    ]
    with (
        patch("api.routers.graph.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.graph.queries.fetch_communities", return_value=comm_rows),
    ):
        resp = client.get("/graph/communities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["community_count"] == 2
    assert body["communities"][1]["is_silo"] is True


# ─── GET /risk/scores ─────────────────────────────────────────────────────────


def test_risk_scores_returns_200(client):
    with (
        patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.risk.queries.fetch_risk_scores", return_value=_RISK_ROWS),
    ):
        resp = client.get("/risk/scores")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["scores"][0]["spof_score"] == 0.82


def test_risk_scores_top_param(client):
    with (
        patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.risk.queries.fetch_risk_scores", return_value=_RISK_ROWS[:1]) as mock_fetch,
    ):
        resp = client.get("/risk/scores?top=1")
    assert resp.status_code == 200
    # Verify top=1 was passed to the query function
    mock_fetch.assert_called_once()
    _, call_top, _ = mock_fetch.call_args[0]
    assert call_top == 1


def test_risk_scores_ordered_desc(client):
    with (
        patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.risk.queries.fetch_risk_scores", return_value=_RISK_ROWS),
    ):
        resp = client.get("/risk/scores")
    scores = [s["spof_score"] for s in resp.json()["scores"]]
    assert scores == sorted(scores, reverse=True)


# ─── GET /risk/critical-nodes ─────────────────────────────────────────────────


def test_critical_nodes_returns_200(client):
    with patch("api.routers.risk.queries.fetch_critical_nodes", return_value=[_RISK_ROWS[0]]):
        resp = client.get("/risk/critical-nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["scores"][0]["flag"] == "critical"


def test_critical_nodes_empty_when_none_critical(client):
    with patch("api.routers.risk.queries.fetch_critical_nodes", return_value=[]):
        resp = client.get("/risk/critical-nodes")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ─── GET /risk/employee/{id}/history ──────────────────────────────────────────


def test_employee_risk_history_returns_200(client):
    history_rows = [
        {"scored_at": date(2025, 4, 18), "spof_score": 0.60, "entropy_trend": -0.05, "flag": "warning"},
        {"scored_at": date(2025, 4, 25), "spof_score": 0.82, "entropy_trend": -0.15, "flag": "critical"},
    ]
    with patch("api.routers.risk.queries.fetch_employee_risk_history", return_value=history_rows):
        resp = client.get(f"/risk/employee/{_EMP_A}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["employee_id"] == _EMP_A
    assert len(body["history"]) == 2


def test_employee_risk_history_empty_is_valid(client):
    with patch("api.routers.risk.queries.fetch_employee_risk_history", return_value=[]):
        resp = client.get(f"/risk/employee/{_EMP_A}/history?days=7")
    assert resp.status_code == 200
    assert resp.json()["history"] == []


# ─── POST /risk/simulate ──────────────────────────────────────────────────────


def test_simulate_star_graph_betweenness_increases(client):
    """Removing the center of a star graph increases avg betweenness (components)."""
    import networkx as nx

    center = str(uuid.uuid4())
    leaves = [str(uuid.uuid4()) for _ in range(4)]

    def mock_load_raw_edges(*args, **kwargs):
        rows = []
        for leaf in leaves:
            rows.append((center, leaf, 1.0, "Engineering", "Engineering"))
            rows.append((leaf, center, 1.0, "Engineering", "Engineering"))
        return rows

    with (
        patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.risk.load_raw_edges", side_effect=mock_load_raw_edges),
    ):
        resp = client.post(
            "/risk/simulate",
            json={"remove_employee_id": center},
        )
    assert resp.status_code == 200
    body = resp.json()
    # Removing center should create 4 isolated nodes → components increase
    assert body["after"]["weakly_connected_components"] > body["before"]["weakly_connected_components"]
    assert body["impact"]["components_delta"] > 0


def test_simulate_404_when_no_snapshot(client):
    with patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=None):
        resp = client.post(
            "/risk/simulate",
            json={"remove_employee_id": _EMP_A},
        )
    assert resp.status_code == 404


def test_simulate_404_when_employee_not_in_graph(client):
    with (
        patch("api.routers.risk.queries.fetch_latest_snapshot_date", return_value=_SNAPSHOT_DATE),
        patch("api.routers.risk.load_raw_edges", return_value=[
            (_EMP_A, _EMP_B, 1.0, "Engineering", "Sales"),
        ]),
    ):
        resp = client.post(
            "/risk/simulate",
            json={"remove_employee_id": _EMP_C},  # not in graph
        )
    assert resp.status_code == 404


# ─── GET /alerts/silos ────────────────────────────────────────────────────────


def test_silo_alerts_returns_200(client):
    with patch("api.routers.alerts.queries.fetch_silo_alerts", return_value=[_ALERT_ROW]):
        resp = client.get("/alerts/silos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["alerts"][0]["type"] == "silo"


def test_silo_alerts_empty_is_valid(client):
    with patch("api.routers.alerts.queries.fetch_silo_alerts", return_value=[]):
        resp = client.get("/alerts/silos")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ─── GET /alerts/entropy ──────────────────────────────────────────────────────


def test_entropy_alerts_returns_200(client):
    entropy_alert = {**_ALERT_ROW, "type": "connectivity_anomaly", "severity": "high"}
    with patch("api.routers.alerts.queries.fetch_entropy_alerts", return_value=[entropy_alert]):
        resp = client.get("/alerts/entropy")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ─── GET /alerts/history ──────────────────────────────────────────────────────


def test_alert_history_returns_200(client):
    history_alerts = [
        {**_ALERT_ROW, "resolved": True, "resolved_at": datetime(2025, 4, 26, tzinfo=timezone.utc)},
        _ALERT_ROW,
    ]
    with patch("api.routers.alerts.queries.fetch_alert_history", return_value=history_alerts):
        resp = client.get("/alerts/history?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


def test_alert_history_days_param_validated(client):
    """days=0 is below the minimum (ge=1) and must return 422."""
    resp = client.get("/alerts/history?days=0")
    assert resp.status_code == 422
