"""Connector health registry — singleton tracking live connector state.

The registry is populated on startup (in the FastAPI lifespan or when connectors
are enabled) and queried by GET /connectors/status.

Thread-safety: the registry uses a threading.Lock for all mutations so it is
safe to update from background producer threads and read from API request threads
concurrently.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar


@dataclass
class ConnectorHealth:
    """Health snapshot for a single connector."""

    channel: str
    enabled: bool
    healthy: bool
    last_event_at: datetime | None = None
    events_published: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "enabled": self.enabled,
            "healthy": self.healthy,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "events_published": self.events_published,
            "error": self.error,
        }


class ConnectorRegistry:
    """Singleton registry for connector health state.

    Usage:
        registry = ConnectorRegistry.get()
        registry.register(ConnectorHealth(channel="slack", enabled=True, healthy=True))
        registry.record_event("slack")
        statuses = registry.all()
    """

    _instance: ClassVar[ConnectorRegistry | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        self._state: dict[str, ConnectorHealth] = {}
        self._state_lock = threading.Lock()

    # ── Singleton access ──────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> ConnectorRegistry:
        """Return the process-wide singleton, creating it on first call."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._bootstrap_from_env()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (test isolation only)."""
        with cls._lock:
            cls._instance = None

    # ── State mutations ───────────────────────────────────────────────────────

    def register(self, health: ConnectorHealth) -> None:
        """Upsert the health record for a connector channel."""
        with self._state_lock:
            self._state[health.channel] = health

    def set_healthy(self, channel: str, healthy: bool, error: str | None = None) -> None:
        """Update the healthy flag and optional error message for a channel."""
        with self._state_lock:
            entry = self._state.get(channel)
            if entry is not None:
                entry.healthy = healthy
                entry.error = error

    def record_event(self, channel: str) -> None:
        """Increment events_published and update last_event_at for a channel."""
        with self._state_lock:
            entry = self._state.get(channel)
            if entry is not None:
                entry.events_published += 1
                entry.last_event_at = datetime.now(tz=timezone.utc)

    # ── Queries ───────────────────────────────────────────────────────────────

    def all(self) -> list[ConnectorHealth]:
        """Return a snapshot of all registered connector health records."""
        with self._state_lock:
            return list(self._state.values())

    def get_channel(self, channel: str) -> ConnectorHealth | None:
        with self._state_lock:
            return self._state.get(channel)

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def _bootstrap_from_env(self) -> None:
        """Pre-register all known channels based on ENABLE_* env flags.

        Connectors start as healthy=False until their connect() succeeds.
        """
        channels = {
            "slack":  "ENABLE_SLACK",
            "teams":  "ENABLE_TEAMS",
            "jira":   "ENABLE_JIRA",
            "github": "ENABLE_GITHUB",
        }
        for channel, env_key in channels.items():
            enabled = os.environ.get(env_key, "false").lower() in ("true", "1", "yes")
            self._state[channel] = ConnectorHealth(
                channel=channel,
                enabled=enabled,
                healthy=False,
                error="not started" if enabled else None,
            )
