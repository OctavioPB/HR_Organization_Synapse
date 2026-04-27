"""Redis pub/sub broadcaster for WebSocket alert fanout.

Architecture:
  - Publish path: POST /internal/alerts/broadcast → publish() → Redis channel
  - Subscribe path: start_subscriber() (asyncio background task) → manager.broadcast()
  - Graceful degradation: if Redis is unavailable, publish() falls back to
    direct in-process broadcast (works correctly in single-worker mode).

Redis channel: org.alerts.live

Multi-worker note:
  Each Uvicorn worker runs its own subscriber, so every worker's ConnectionManager
  receives the message and broadcasts to its own set of WS clients.
  This is correct: each client is connected to exactly one worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_CHANNEL = "org.alerts.live"
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_RETRY_DELAY = 5  # seconds before reconnecting to Redis after failure


async def _get_async_redis():
    """Return a connected async Redis client or None if unavailable."""
    try:
        import redis.asyncio as aioredis  # redis-py ≥ 4.2
        client = aioredis.Redis.from_url(_REDIS_URL, decode_responses=True)
        await client.ping()
        return client
    except Exception as exc:
        logger.debug("Async Redis unavailable (%s) — pub/sub disabled", exc)
        return None


async def publish(message: dict[str, Any], manager) -> int:
    """Publish an alert message to all connected WebSocket clients.

    Tries Redis pub/sub first for multi-worker support; falls back to
    direct in-process broadcast if Redis is unavailable.

    Args:
        message: JSON-serialisable dict to broadcast.
        manager: ConnectionManager singleton.

    Returns:
        Number of WebSocket clients notified in this process (0 when Redis
        is used, because delivery happens asynchronously via the subscriber).
    """
    client = await _get_async_redis()
    if client is not None:
        try:
            text = json.dumps(message, default=str)
            await client.publish(_CHANNEL, text)
            return 0  # subscriber handles delivery
        except Exception as exc:
            logger.warning("Redis publish failed (%s) — falling back to direct broadcast", exc)
        finally:
            await client.aclose()

    # Fallback: direct in-process broadcast (single-worker mode)
    return await manager.broadcast(message)


async def start_subscriber(manager) -> None:
    """Long-running background task: subscribe to Redis and forward to WS clients.

    Reconnects automatically on Redis failure with exponential-ish backoff.
    Runs until the application shuts down (cancelled by lifespan context).
    """
    while True:
        client = None
        pubsub = None
        try:
            import redis.asyncio as aioredis

            client = aioredis.Redis.from_url(_REDIS_URL, decode_responses=True)
            pubsub = client.pubsub()
            await pubsub.subscribe(_CHANNEL)
            logger.info("Redis subscriber connected — listening on channel '%s'", _CHANNEL)

            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                    count = await manager.broadcast(data)
                    if count:
                        logger.debug("WS broadcast: %d clients notified", count)
                except Exception as exc:
                    logger.warning("WS broadcast error: %s", exc)

        except asyncio.CancelledError:
            logger.info("Redis subscriber task cancelled — shutting down.")
            break
        except Exception as exc:
            logger.warning(
                "Redis subscriber error (%s) — reconnecting in %ds", exc, _RETRY_DELAY
            )
            await asyncio.sleep(_RETRY_DELAY)
        finally:
            if pubsub:
                with __import__("contextlib").suppress(Exception):
                    await pubsub.unsubscribe(_CHANNEL)
            if client:
                with __import__("contextlib").suppress(Exception):
                    await client.aclose()
