"""Router: WebSocket endpoints for real-time alert streaming.

Endpoint:
    WS /alerts/live

Protocol (JSON messages):
  Client → Server:
    "ping"                         → server responds {"type": "pong"}

  Server → Client (on connect):
    {"type": "initial", "alerts": [...], "connection_count": N}

  Server → Client (on new alert):
    {"type": "alert", "alerts": [...], "source": "<dag_id>", "timestamp": "..."}

  Server → Client (keep-alive, every 25s):
    {"type": "ping"}

Connection lifecycle:
  1. Client connects → server sends last 10 alerts as initial payload.
  2. Server loops: wait up to 25s for client message.
     - If "ping" received → respond {"type": "pong"}.
     - If timeout → send keep-alive {"type": "ping"}.
     - If disconnect detected → exit loop.
  3. Manager removes dead connections automatically on next broadcast.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from api.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

_KEEPALIVE_TIMEOUT = 25  # seconds between server-side pings
_INITIAL_ALERT_LIMIT = 10


def _get_recent_alerts(limit: int) -> list[dict]:
    """Fetch the most recent alerts for the initial payload sent on connect.

    Uses a fresh DB connection to avoid holding a connection during the WS loop.
    Returns an empty list silently on any DB error (WS must not fail on startup).
    """
    try:
        from ingestion.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id::text, fired_at, type, severity,
                        affected_entities, details, resolved, resolved_at
                    FROM alerts
                    ORDER BY fired_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("WS initial payload: could not fetch alerts: %s", exc)
        return []


@router.websocket("/alerts/live")
async def websocket_alerts(ws: WebSocket) -> None:
    """Real-time alert stream over WebSocket.

    Sends the last 10 alerts immediately on connect, then streams new alerts
    as they are broadcast by Airflow DAGs via POST /internal/alerts/broadcast.
    """
    await manager.connect(ws)
    logger.info(
        "WS /alerts/live: new client connected (total: %d)", manager.connection_count
    )

    try:
        # Send recent alerts so the client doesn't start with a blank slate
        recent = _get_recent_alerts(_INITIAL_ALERT_LIMIT)
        await ws.send_json({
            "type": "initial",
            "alerts": recent,
            "connection_count": manager.connection_count,
        })

        # Keep-alive loop: wait for client messages or send periodic pings
        while True:
            if ws.client_state == WebSocketState.DISCONNECTED:
                break
            try:
                msg = await asyncio.wait_for(
                    ws.receive_text(), timeout=_KEEPALIVE_TIMEOUT
                )
                if msg.strip() == "ping":
                    await ws.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Send server-side keep-alive ping
                await ws.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.debug("WS receive error: %s", exc)
                break

    finally:
        await manager.disconnect(ws)
        logger.info(
            "WS /alerts/live: client disconnected (remaining: %d)", manager.connection_count
        )
