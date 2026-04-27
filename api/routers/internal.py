"""Router: /internal — machine-to-machine endpoints not exposed publicly.

These endpoints are called by Airflow DAGs and internal services.
In production, secure this router behind a network policy or API key
(INTERNAL_API_KEY env var) so it is not reachable from the public internet.

Endpoints:
    POST /internal/alerts/broadcast   — push new alerts to all WS clients
    GET  /internal/ws/status          — number of active WebSocket connections
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from api.models.schemas import AlertItem
from api.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")


def _check_api_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Reject requests with wrong API key when INTERNAL_API_KEY is configured.

    If INTERNAL_API_KEY env var is empty (default), no authentication is enforced
    (suitable for local dev / single-tenant deployments where the endpoint is
    network-isolated).
    """
    if _INTERNAL_API_KEY and x_internal_key != _INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid internal API key.")


class BroadcastRequest(BaseModel):
    source: str = "airflow"
    alerts: list[AlertItem] = []
    metadata: dict[str, Any] = {}


class BroadcastResponse(BaseModel):
    broadcast: int
    connections: int
    timestamp: str


@router.post("/alerts/broadcast", response_model=BroadcastResponse)
async def broadcast_alerts(
    payload: BroadcastRequest,
    _: None = Depends(_check_api_key),
) -> BroadcastResponse:
    """Push new alerts to all connected WebSocket clients.

    Called by Airflow DAGs after computing new SPOF scores, silos, or anomalies.
    The payload is broadcast to all connected clients as a single JSON message.
    """
    from api.ws.broadcaster import publish

    message: dict[str, Any] = {
        "type": "alert",
        "source": payload.source,
        "alerts": [a.model_dump(mode="json") for a in payload.alerts],
        "metadata": payload.metadata,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    notified = await publish(message, manager)
    logger.info(
        "internal/alerts/broadcast: source=%s alerts=%d ws_clients=%d",
        payload.source, len(payload.alerts), manager.connection_count,
    )

    return BroadcastResponse(
        broadcast=len(payload.alerts),
        connections=notified if notified else manager.connection_count,
        timestamp=message["timestamp"],
    )


@router.get("/ws/status")
async def ws_status(_: None = Depends(_check_api_key)) -> dict:
    """Return the number of active WebSocket connections in this worker."""
    return {
        "active_connections": manager.connection_count,
        "channel": "org.alerts.live",
    }
