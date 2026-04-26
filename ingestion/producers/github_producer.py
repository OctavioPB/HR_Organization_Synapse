"""GitHub metadata connector — webhook receiver (push-based).

Privacy model:
  - Only captures: who reviewed whose PR, who requested a review, who was assigned.
  - NO access to PR diff content, NO commit messages, NO issue body text.
  - Required webhook events: pull_request, pull_request_review, pull_request_review_requested

Two components work together:
  1. GitHubProducer: parses webhook payloads and queues CollaborationEvent objects.
  2. FastAPI route (api/routers/connectors.py): receives POST /connectors/github/webhook,
     verifies the HMAC-SHA256 signature, and calls parse_webhook_payload().

Environment variables:
    GITHUB_WEBHOOK_SECRET   Webhook secret configured in GitHub (for HMAC verification)
    GITHUB_EMPLOYEE_MAP     JSON dict mapping GitHub login → employee UUID (optional)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import queue
import time
import uuid
from datetime import datetime, timezone
from typing import Iterator

from ingestion.producers.base_producer import BaseProducer
from ingestion.producers.registry import ConnectorRegistry
from ingestion.schemas.collaboration_event import CollaborationEvent

logger = logging.getLogger(__name__)


class GitHubProducer(BaseProducer):
    """GitHub collaboration metadata connector.

    Passive: no polling — events arrive via POST /connectors/github/webhook.
    The FastAPI route calls parse_webhook_payload() which returns a
    CollaborationEvent (or None). The router then calls enqueue_webhook_event()
    to push it into the stream.

    stream_events() blocks on the internal queue and yields events as they arrive.
    """

    channel = "github"

    def __init__(self) -> None:
        self._webhook_secret: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        self._employee_map: dict[str, str] = {}
        _raw_map = os.environ.get("GITHUB_EMPLOYEE_MAP", "{}")
        try:
            import json
            self._employee_map = json.loads(_raw_map)
        except Exception:
            logger.warning("GITHUB_EMPLOYEE_MAP is not valid JSON; ignoring")
        self._event_queue: queue.Queue[CollaborationEvent] = queue.Queue()
        self._running = False

    # ── BaseProducer contract ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Verify webhook secret is configured (no network call — webhook is inbound)."""
        if not self._webhook_secret:
            logger.warning(
                "GITHUB_WEBHOOK_SECRET is not set; webhook signature verification disabled"
            )
        ConnectorRegistry.get().set_healthy("github", healthy=True)
        logger.info("GitHub connector: ready to receive webhook events")

    def stream_events(self) -> Iterator[CollaborationEvent]:
        """Block on the internal queue and yield events as webhooks arrive."""
        self._running = True
        while self._running:
            try:
                event = self._event_queue.get(timeout=5.0)
                ConnectorRegistry.get().record_event("github")
                yield event
            except queue.Empty:
                continue

    def disconnect(self) -> None:
        self._running = False

    def health_check(self) -> dict:
        try:
            self.connect()
            return {"channel": self.channel, "healthy": True, "error": None}
        except Exception as exc:
            return {"channel": self.channel, "healthy": False, "error": str(exc)}

    # ── Webhook signature verification ────────────────────────────────────────

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        """Verify GitHub's HMAC-SHA256 webhook signature.

        GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>
        Rejects if secret is not set and a signature was provided.
        """
        if not self._webhook_secret:
            # No secret configured — skip verification (dev mode only)
            return True

        if not signature_header.startswith("sha256="):
            logger.warning("GitHub webhook: missing or malformed signature header")
            return False

        expected = hmac.new(
            self._webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        received = signature_header[len("sha256="):]
        return hmac.compare_digest(expected, received)

    # ── Webhook parsing ───────────────────────────────────────────────────────

    def parse_webhook_payload(self, payload: dict) -> CollaborationEvent | None:
        """Convert a GitHub webhook payload into a CollaborationEvent.

        Handles:
          - pull_request (action=review_requested): requester → reviewer
          - pull_request (action=assigned): sender → assignee
          - pull_request_review (action=submitted): reviewer → PR author
          - pull_request_review_comment (ignored — would expose file path context)

        Returns None for unsupported events or incomplete payloads.
        """
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        sender = payload.get("sender", {})

        # Event: someone submitted a PR review
        review = payload.get("review", {})
        if review and action == "submitted":
            reviewer_login = review.get("user", {}).get("login")
            pr_author_login = pr.get("user", {}).get("login")
            if reviewer_login and pr_author_login and reviewer_login != pr_author_login:
                return self._build_event(
                    source=reviewer_login,
                    target=pr_author_login,
                    direction="reviewed",
                    payload=payload,
                )

        # Event: review requested for a PR
        if action == "review_requested":
            requester_login = sender.get("login")
            reviewer = payload.get("requested_reviewer", {}).get("login")
            if requester_login and reviewer and requester_login != reviewer:
                return self._build_event(
                    source=requester_login,
                    target=reviewer,
                    direction="assigned",
                    payload=payload,
                )

        # Event: PR assigned to someone
        if action == "assigned":
            assignee_login = payload.get("assignee", {}).get("login")
            sender_login = sender.get("login")
            if sender_login and assignee_login and sender_login != assignee_login:
                return self._build_event(
                    source=sender_login,
                    target=assignee_login,
                    direction="assigned",
                    payload=payload,
                )

        return None

    def enqueue_webhook_event(self, event: CollaborationEvent) -> None:
        """Push a parsed event into the stream queue (called by the FastAPI route)."""
        self._event_queue.put(event)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_employee_id(self, github_login: str) -> str:
        return self._employee_map.get(github_login, github_login)

    def _extract_timestamp(self, payload: dict) -> datetime:
        pr = payload.get("pull_request", {})
        review = payload.get("review", {})
        raw = (
            review.get("submitted_at")
            or pr.get("updated_at")
            or pr.get("created_at")
        )
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(tz=timezone.utc)

    def _build_event(
        self,
        source: str,
        target: str,
        direction: str,
        payload: dict,
    ) -> CollaborationEvent | None:
        if source == target:
            return None
        return CollaborationEvent(
            event_id=str(uuid.uuid4()),
            source_employee_id=self._resolve_employee_id(source),
            target_employee_id=self._resolve_employee_id(target),
            channel="github",
            direction=direction,  # type: ignore[arg-type]
            department_source="unknown",
            department_target="unknown",
            timestamp=self._extract_timestamp(payload),
            weight=1.0,
        )
