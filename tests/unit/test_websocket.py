"""Unit tests for F5 — Real-Time Alert Engine (WebSockets).

Tests cover:
  - ConnectionManager: connect, disconnect, broadcast, dead-connection cleanup
  - WS /alerts/live: connection lifecycle, initial payload, ping/pong
  - POST /internal/alerts/broadcast: happy path, key auth, empty payload
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketState


# ─── ConnectionManager ────────────────────────────────────────────────────────


class TestConnectionManager:
    def _make_manager(self):
        # Re-import to get a fresh instance for each test
        from api.ws.manager import ConnectionManager
        return ConnectionManager()

    def _fake_ws(self, fail_send: bool = False) -> MagicMock:
        ws = MagicMock()
        ws.client_state = WebSocketState.CONNECTED
        ws.accept = AsyncMock()
        if fail_send:
            ws.send_text = AsyncMock(side_effect=RuntimeError("connection closed"))
        else:
            ws.send_text = AsyncMock()
        return ws

    def test_connect_adds_to_active(self):
        mgr = self._make_manager()
        ws = self._fake_ws()
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws))
        assert mgr.connection_count == 1
        ws.accept.assert_awaited_once()

    def test_disconnect_removes_from_active(self):
        mgr = self._make_manager()
        ws = self._fake_ws()
        asyncio.get_event_loop().run_until_complete(mgr.connect(ws))
        asyncio.get_event_loop().run_until_complete(mgr.disconnect(ws))
        assert mgr.connection_count == 0

    def test_disconnect_unknown_ws_is_safe(self):
        mgr = self._make_manager()
        ws = self._fake_ws()
        # Disconnecting something that was never connected should not raise
        asyncio.get_event_loop().run_until_complete(mgr.disconnect(ws))
        assert mgr.connection_count == 0

    def test_broadcast_reaches_all_connections(self):
        mgr = self._make_manager()
        ws1 = self._fake_ws()
        ws2 = self._fake_ws()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.connect(ws1))
        loop.run_until_complete(mgr.connect(ws2))
        msg = {"type": "alert", "alerts": []}
        notified = loop.run_until_complete(mgr.broadcast(msg))
        assert notified == 2
        ws1.send_text.assert_awaited_once_with(json.dumps(msg, default=str))
        ws2.send_text.assert_awaited_once_with(json.dumps(msg, default=str))

    def test_broadcast_removes_dead_connections(self):
        mgr = self._make_manager()
        ws_ok = self._fake_ws(fail_send=False)
        ws_dead = self._fake_ws(fail_send=True)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.connect(ws_ok))
        loop.run_until_complete(mgr.connect(ws_dead))
        assert mgr.connection_count == 2

        loop.run_until_complete(mgr.broadcast({"type": "ping"}))
        # Dead connection should have been removed
        assert mgr.connection_count == 1

    def test_broadcast_empty_returns_zero(self):
        mgr = self._make_manager()
        result = asyncio.get_event_loop().run_until_complete(
            mgr.broadcast({"type": "ping"})
        )
        assert result == 0

    def test_multiple_disconnects_same_ws(self):
        mgr = self._make_manager()
        ws = self._fake_ws()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(mgr.connect(ws))
        loop.run_until_complete(mgr.disconnect(ws))
        loop.run_until_complete(mgr.disconnect(ws))  # second disconnect is safe
        assert mgr.connection_count == 0


# ─── WS endpoint ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ws_client():
    """TestClient with WebSocket support for the main app.

    Patches DB calls so no actual Postgres connection is needed.
    """
    with patch("api.routers.ws._get_recent_alerts", return_value=[]):
        from api.main import app
        yield TestClient(app, raise_server_exceptions=False)


class TestWebSocketEndpoint:
    def test_connection_established(self, ws_client):
        with ws_client.websocket_connect("/alerts/live") as ws:
            # Should receive initial message immediately
            msg = ws.receive_json()
            assert msg["type"] == "initial"
            assert "alerts" in msg
            assert "connection_count" in msg

    def test_ping_pong(self, ws_client):
        with ws_client.websocket_connect("/alerts/live") as ws:
            ws.receive_json()   # consume initial message
            ws.send_text("ping")
            response = ws.receive_json()
            assert response["type"] == "pong"

    def test_initial_alerts_list_present(self, ws_client):
        with patch("api.routers.ws._get_recent_alerts", return_value=[
            {
                "id": "a1",
                "fired_at": "2025-05-01T10:00:00Z",
                "type": "spof_critical",
                "severity": "high",
                "affected_entities": {},
                "details": "Alice is a SPOF",
                "resolved": False,
                "resolved_at": None,
            }
        ]):
            with ws_client.websocket_connect("/alerts/live") as ws:
                msg = ws.receive_json()
        assert msg["type"] == "initial"
        assert len(msg["alerts"]) == 1
        assert msg["alerts"][0]["type"] == "spof_critical"


# ─── Internal broadcast endpoint ─────────────────────────────────────────────


_SAMPLE_ALERT: dict[str, Any] = {
    "id": "b1b1b1b1-0000-0000-0000-000000000001",
    "fired_at": datetime.utcnow().isoformat(),
    "type": "spof_critical",
    "severity": "high",
    "affected_entities": {"employee_id": "emp-001"},
    "details": "High SPOF score detected",
    "resolved": False,
    "resolved_at": None,
}


@pytest.fixture()
def api_client():
    from api.main import app
    from api.deps import get_db
    mock_conn = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_conn
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


class TestInternalBroadcastEndpoint:
    def test_broadcast_returns_200(self, api_client):
        payload = {
            "source": "graph_builder_dag",
            "alerts": [_SAMPLE_ALERT],
            "metadata": {"snapshot_date": "2025-05-01"},
        }
        with patch("api.ws.broadcaster.publish", new_callable=AsyncMock, return_value=1):
            resp = api_client.post("/internal/alerts/broadcast", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["broadcast"] == 1
        assert "timestamp" in body

    def test_broadcast_empty_alerts_returns_200(self, api_client):
        payload = {"source": "test", "alerts": [], "metadata": {}}
        with patch("api.ws.broadcaster.publish", new_callable=AsyncMock, return_value=0):
            resp = api_client.post("/internal/alerts/broadcast", json=payload)
        assert resp.status_code == 200
        assert resp.json()["broadcast"] == 0

    def test_ws_status_returns_connection_count(self, api_client):
        from api.ws.manager import manager
        resp = api_client.get("/internal/ws/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "active_connections" in body
        assert body["active_connections"] == manager.connection_count

    def test_api_key_rejected_when_configured(self, api_client, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_KEY", "secret-key")
        # Re-import routers to pick up the new env var
        import importlib
        import api.routers.internal as internal_mod
        importlib.reload(internal_mod)

        payload = {"source": "test", "alerts": [], "metadata": {}}
        resp = api_client.post(
            "/internal/alerts/broadcast",
            json=payload,
            headers={"X-Internal-Key": "wrong-key"},
        )
        assert resp.status_code == 403

    def test_health_includes_ws_connections(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "websocket_connections" in body
        assert isinstance(body["websocket_connections"], int)
