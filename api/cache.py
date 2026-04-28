"""Redis cache helpers for the Org Synapse API.

Design:
  - Graceful degradation: every function silently returns None / is a no-op
    when Redis is unavailable. The API never fails because of cache errors.
  - JSON serialisation: values are stored as UTF-8 JSON strings so they can
    be inspected with redis-cli without extra decoding.
  - Cache keys use a versioned namespace (CACHE_VERSION) so schema changes
    can be invalidated atomically by bumping the constant.

Environment variables:
    REDIS_URL        Redis connection URL (default: redis://localhost:6379/0)
    CACHE_TTL_SEC    Default TTL in seconds for snapshot cache (default: 3600)
    CACHE_ENABLED    Set to 'false' to disable caching without removing Redis
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

CACHE_VERSION = "v1"
_DEFAULT_TTL = int(os.environ.get("CACHE_TTL_SEC", "3600"))
_CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() not in ("false", "0", "no")

_client = None  # lazily initialised


def _get_client():
    """Return a connected Redis client, or None if unavailable."""
    global _client
    if not _CACHE_ENABLED:
        return None
    if _client is not None:
        return _client
    try:
        import redis  # optional dependency

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        candidate = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
        candidate.ping()
        _client = candidate
        logger.info("Redis cache connected: %s", url)
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — caching disabled", exc)
        _client = None
    return _client


def reset_client() -> None:
    """Force re-connection on next call (used in tests and after config changes)."""
    global _client
    _client = None


# ── Public helpers ─────────────────────────────────────────────────────────────


def make_key(*parts: str) -> str:
    """Build a namespaced cache key.

    Example: make_key("snapshot", "2025-04-25", "30") → "org-synapse:v1:snapshot:2025-04-25:30"
    """
    return "org-synapse:" + CACHE_VERSION + ":" + ":".join(parts)


def get(key: str) -> Any | None:
    """Retrieve a JSON-decoded value from the cache.

    Returns None on cache miss, Redis error, or when caching is disabled.
    """
    r = _get_client()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.debug("Cache GET error key=%s: %s", key, exc)
        return None


def set(key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
    """Serialise value as JSON and store it with the given TTL.

    Silently swallows all errors — cache writes are best-effort.
    """
    r = _get_client()
    if r is None:
        return
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as exc:
        logger.debug("Cache SET error key=%s: %s", key, exc)


def delete(key: str) -> None:
    """Remove a key from the cache (used for explicit invalidation)."""
    r = _get_client()
    if r is None:
        return
    try:
        r.delete(key)
    except Exception as exc:
        logger.debug("Cache DELETE error key=%s: %s", key, exc)


def invalidate_snapshot(snapshot_date: str) -> None:
    """Delete all cache entries for a given snapshot date across all window sizes.

    Called by the Airflow graph_builder_dag after a new snapshot is written so
    the API immediately serves fresh data instead of stale cached responses.
    """
    r = _get_client()
    if r is None:
        return
    try:
        pattern = make_key("snapshot", snapshot_date, "*")
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
            logger.info("Cache: invalidated %d keys for snapshot_date=%s", len(keys), snapshot_date)
    except Exception as exc:
        logger.debug("Cache invalidation error for %s: %s", snapshot_date, exc)


def flush_all() -> int:
    """Delete every org-synapse cache key. Call after a DB reset or full reseed.

    Returns the number of keys deleted (0 if Redis is unavailable).
    """
    r = _get_client()
    if r is None:
        return 0
    try:
        pattern = f"org-synapse:{CACHE_VERSION}:*"
        keys = r.keys(pattern)
        if keys:
            count = r.delete(*keys)
            logger.info("Cache: flushed %d keys", count)
            return int(count)
        return 0
    except Exception as exc:
        logger.debug("Cache flush error: %s", exc)
        return 0


def health() -> dict:
    """Return a cache health dict suitable for inclusion in /health responses."""
    r = _get_client()
    if r is None:
        return {"cache": "unavailable", "enabled": _CACHE_ENABLED}
    try:
        info = r.info("server")
        return {
            "cache": "healthy",
            "enabled": True,
            "redis_version": info.get("redis_version"),
        }
    except Exception as exc:
        return {"cache": "error", "enabled": True, "error": str(exc)}
