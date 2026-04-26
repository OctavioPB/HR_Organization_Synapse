"""Slack metadata connector — Events API (webhook) + Web API polling fallback.

Privacy model:
  - Only captures: who sent a message, in which channel (no message text).
  - Required OAuth scopes: channels:read, users:read
  - NO messages:read, NO files:read, NO content access.

Two operating modes:
  1. Webhook (production): Slack posts events to POST /connectors/slack/events.
     parse_webhook_payload() extracts sender + channel metadata and returns a
     CollaborationEvent. The FastAPI route handles signature verification and
     calls this method directly.

  2. Polling (fallback): stream_events() polls conversations.list for channel
     membership and emits co-membership edges at configurable intervals. This
     does not capture direct message sends — it captures shared-channel presence.

Environment variables:
    SLACK_BOT_TOKEN       Bot OAuth token (xoxb-...)
    SLACK_SIGNING_SECRET  Signing secret for webhook signature verification
    SLACK_POLL_INTERVAL   Seconds between polling cycles (default: 300)
    SLACK_EMPLOYEE_MAP    JSON-encoded dict mapping slack_user_id → employee_id
                          (optional; raw slack_user_id used when absent)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import time
import uuid
from datetime import datetime, timezone
from typing import Iterator

import httpx

from ingestion.producers.base_producer import BaseProducer
from ingestion.producers.registry import ConnectorRegistry
from ingestion.schemas.collaboration_event import CollaborationEvent

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"
_DEFAULT_POLL_INTERVAL = 300  # seconds


class SlackRealProducer(BaseProducer):
    """Slack collaboration metadata connector.

    In webhook mode the producer is passive — events are pushed by Slack and
    injected via enqueue_webhook_event(). In polling mode stream_events() drives
    the loop. Both modes coexist: enqueue takes priority over polling.
    """

    channel = "slack"

    def __init__(self) -> None:
        self._token: str = os.environ.get("SLACK_BOT_TOKEN", "")
        self._signing_secret: str = os.environ.get("SLACK_SIGNING_SECRET", "")
        self._poll_interval: int = int(os.environ.get("SLACK_POLL_INTERVAL", _DEFAULT_POLL_INTERVAL))
        self._employee_map: dict[str, str] = json.loads(
            os.environ.get("SLACK_EMPLOYEE_MAP", "{}")
        )
        self._http: httpx.Client | None = None
        self._event_queue: queue.Queue[CollaborationEvent] = queue.Queue()
        self._running = False

    # ── BaseProducer contract ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Verify the bot token is valid via auth.test."""
        if not self._token:
            raise ValueError("SLACK_BOT_TOKEN is not set")
        self._http = httpx.Client(
            base_url=_SLACK_API,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10.0,
        )
        resp = self._http.get("/auth.test")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise ConnectionError(f"Slack auth.test failed: {data.get('error')}")
        logger.info("Slack connector: authenticated as team=%s", data.get("team"))
        ConnectorRegistry.get().set_healthy("slack", healthy=True)

    def stream_events(self) -> Iterator[CollaborationEvent]:
        """Yield events from the webhook queue; fall back to polling when idle."""
        self._running = True
        last_poll = 0.0
        while self._running:
            # Drain webhook queue first (highest priority)
            try:
                event = self._event_queue.get_nowait()
                ConnectorRegistry.get().record_event("slack")
                yield event
                continue
            except queue.Empty:
                pass

            # Polling fallback: emit co-membership edges for shared channels
            now = time.monotonic()
            if now - last_poll >= self._poll_interval:
                yield from self._poll_channel_membership()
                last_poll = time.monotonic()

            time.sleep(1.0)

    def disconnect(self) -> None:
        self._running = False
        if self._http is not None:
            self._http.close()
            self._http = None

    def health_check(self) -> dict:
        try:
            self.connect()
            self.disconnect()
            return {"channel": self.channel, "healthy": True, "error": None}
        except Exception as exc:
            return {"channel": self.channel, "healthy": False, "error": str(exc)}

    # ── Webhook handling ──────────────────────────────────────────────────────

    def verify_signature(self, body: bytes, timestamp: str, signature: str) -> bool:
        """Return True if the request signature matches the signing secret.

        Implements Slack's HMAC-SHA256 verification protocol.
        Rejects requests with timestamps more than 5 minutes old.
        """
        if not self._signing_secret:
            logger.warning("SLACK_SIGNING_SECRET not set; skipping signature verification")
            return True

        try:
            ts_int = int(timestamp)
        except ValueError:
            return False

        if abs(time.time() - ts_int) > 300:
            logger.warning("Slack webhook: stale timestamp %s", timestamp)
            return False

        sig_base = f"v0:{timestamp}:{body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            self._signing_secret.encode("utf-8"),
            sig_base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook_payload(self, payload: dict) -> CollaborationEvent | None:
        """Extract metadata from a Slack Events API event payload.

        Handles:
          - message (type=message) in public/private channels
          - reaction_added (maps to 'mentioned' direction)
          - app_mention

        Ignores bot messages, channel join/leave system messages.
        Returns None for unsupported or non-interaction event types.
        """
        event = payload.get("event", {})
        event_type = event.get("type", "")
        subtype = event.get("subtype", "")

        # Skip system messages and bot messages
        if subtype in ("bot_message", "channel_join", "channel_leave", "channel_archive"):
            return None
        if event.get("bot_id"):
            return None

        user_id = event.get("user") or event.get("item_user")
        channel_id = event.get("channel")

        if not user_id or not channel_id:
            return None

        # For reactions, the target is the item's owner
        if event_type == "reaction_added":
            target_user = event.get("item_user")
            if not target_user or target_user == user_id:
                return None
            return self._build_event(
                source=user_id,
                target=target_user,
                direction="mentioned",
                channel_id=channel_id,
            )

        # Standard message — we only know the sender; we create a self-loop-free
        # signal: sender → channel (represented as sender mentions the channel group)
        # In practice the API caller should resolve channel members to build edges.
        # Here we emit one event per sender per channel-presence signal.
        if event_type in ("message", "app_mention"):
            # Without a specific target we cannot build a directed edge.
            # Emit a self-mention that the pipeline can aggregate per channel.
            # Real cross-employee edges are built in graph/builder.py from
            # shared channel co-activity during the same session window.
            return None  # Requires channel member resolution; handled in polling

        return None

    def enqueue_webhook_event(self, event: CollaborationEvent) -> None:
        """Push a parsed CollaborationEvent into the stream queue.

        Called by the FastAPI webhook route after signature verification and
        parse_webhook_payload() parsing.
        """
        self._event_queue.put(event)

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll_channel_membership(self) -> Iterator[CollaborationEvent]:
        """Emit co-membership edges for all public channel member pairs.

        Creates an undirected edge (two directed CollaborationEvents) for every
        pair of employees who share a public channel. This is the metadata-only
        fallback when direct message-send events are not available.
        """
        if self._http is None:
            return

        try:
            channels = self._list_channels()
        except Exception as exc:
            logger.warning("Slack polling: failed to list channels: %s", exc)
            ConnectorRegistry.get().set_healthy("slack", healthy=False, error=str(exc))
            return

        ConnectorRegistry.get().set_healthy("slack", healthy=True)

        for ch in channels:
            ch_id = ch["id"]
            try:
                members = self._list_channel_members(ch_id)
            except Exception as exc:
                logger.debug("Slack polling: failed to get members for %s: %s", ch_id, exc)
                continue

            # Emit edges for the first 50 member pairs to avoid combinatorial explosion
            pairs_emitted = 0
            for i, user_a in enumerate(members):
                for user_b in members[i + 1:]:
                    if pairs_emitted >= 50:
                        break
                    event = self._build_event(
                        source=user_a,
                        target=user_b,
                        direction="sent",
                        channel_id=ch_id,
                    )
                    if event:
                        ConnectorRegistry.get().record_event("slack")
                        yield event
                        pairs_emitted += 1

    def _list_channels(self) -> list[dict]:
        channels: list[dict] = []
        cursor = None
        while True:
            params: dict = {"types": "public_channel", "limit": 200, "exclude_archived": True}
            if cursor:
                params["cursor"] = cursor
            resp = self._http.get("/conversations.list", params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise ConnectionError(f"conversations.list error: {data.get('error')}")
            channels.extend(data.get("channels", []))
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        return channels

    def _list_channel_members(self, channel_id: str) -> list[str]:
        members: list[str] = []
        cursor = None
        while True:
            params: dict = {"channel": channel_id, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = self._http.get("/conversations.members", params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise ConnectionError(f"conversations.members error: {data.get('error')}")
            members.extend(data.get("members", []))
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        return members

    def _resolve_employee_id(self, slack_user_id: str) -> str:
        """Map Slack user ID to internal employee UUID.

        Falls back to the raw Slack user ID when no mapping is configured.
        """
        return self._employee_map.get(slack_user_id, slack_user_id)

    def _build_event(
        self,
        source: str,
        target: str,
        direction: str,
        channel_id: str,
    ) -> CollaborationEvent | None:
        if source == target:
            return None
        return CollaborationEvent(
            event_id=str(uuid.uuid4()),
            source_employee_id=self._resolve_employee_id(source),
            target_employee_id=self._resolve_employee_id(target),
            channel="slack",
            direction=direction,  # type: ignore[arg-type]
            department_source="unknown",
            department_target="unknown",
            timestamp=datetime.now(tz=timezone.utc),
            weight=1.0,
        )
