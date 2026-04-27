"""WebSocket connection manager — tracks active clients and broadcasts messages.

Design:
  - Single in-process singleton (manager) shared by all WebSocket handlers.
  - Thread/coroutine safe via asyncio.Lock.
  - Dead connections are silently removed on the next broadcast attempt.
  - For multi-worker deployments the broadcaster module uses Redis pub/sub so
    all workers receive messages even if the connection lives in a different worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages the set of active WebSocket connections."""

    def __init__(self) -> None:
        self._active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await ws.accept()
        async with self._lock:
            self._active.append(ws)
        logger.debug("WS client connected — total: %d", len(self._active))

    async def disconnect(self, ws: WebSocket) -> None:
        """Remove a WebSocket from the active set."""
        async with self._lock:
            self._active = [w for w in self._active if w is not ws]
        logger.debug("WS client disconnected — total: %d", len(self._active))

    async def broadcast(self, message: dict[str, Any]) -> int:
        """Send a JSON message to all connected clients.

        Silently removes connections that fail to receive (closed/errored).

        Returns:
            Number of clients successfully notified.
        """
        text = json.dumps(message, default=str)
        dead: list[WebSocket] = []

        async with self._lock:
            connections = list(self._active)

        notified = 0
        for ws in connections:
            try:
                await ws.send_text(text)
                notified += 1
            except Exception as exc:
                logger.debug("WS send failed (%s) — marking connection dead", exc)
                dead.append(ws)

        if dead:
            async with self._lock:
                self._active = [w for w in self._active if w not in dead]
            logger.debug("WS: removed %d dead connections", len(dead))

        return notified

    @property
    def connection_count(self) -> int:
        """Number of currently active WebSocket connections."""
        return len(self._active)


# Module-level singleton shared by all routers and the broadcaster.
manager = ConnectionManager()
