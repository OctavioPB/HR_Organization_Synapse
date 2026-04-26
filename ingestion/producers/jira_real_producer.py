"""Jira metadata connector — REST API v3 polling.

Privacy model:
  - Captures: assignee ↔ reporter on issues, user IDs mentioned in comments.
  - NO comment text, NO issue descriptions, NO file attachments.
  - Required Jira permissions: Browse projects (read-only).

The connector polls Jira's REST API for recently updated issues and extracts
only the user IDs involved in assignments and comment authorship. It does NOT
read comment bodies — only the `author.accountId` field from the comments list.

Environment variables:
    JIRA_BASE_URL         e.g. https://yourorg.atlassian.net
    JIRA_EMAIL            Service account email for Basic Auth
    JIRA_API_TOKEN        Jira API token (not password)
    JIRA_PROJECTS         Comma-separated project keys to monitor (e.g. ENG,HR,OPS)
    JIRA_POLL_INTERVAL    Seconds between polling cycles (default: 300)
    JIRA_EMPLOYEE_MAP     JSON dict mapping Jira accountId → employee UUID (optional)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

from ingestion.producers.base_producer import BaseProducer
from ingestion.producers.registry import ConnectorRegistry
from ingestion.schemas.collaboration_event import CollaborationEvent

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 300  # seconds
_DEFAULT_LOOKBACK_MINUTES = 10


class JiraRealProducer(BaseProducer):
    """Jira collaboration metadata connector.

    Polls for issues updated in the last N minutes and extracts:
      - assignee ↔ reporter directed edge (direction="assigned")
      - comment author → assignee edge (direction="mentioned")

    Both edges use only Jira accountId values (no names, no email addresses).
    """

    channel = "jira"

    def __init__(self) -> None:
        self._base_url: str = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        self._email: str = os.environ.get("JIRA_EMAIL", "")
        self._api_token: str = os.environ.get("JIRA_API_TOKEN", "")
        self._projects: list[str] = [
            p.strip()
            for p in os.environ.get("JIRA_PROJECTS", "").split(",")
            if p.strip()
        ]
        self._poll_interval: int = int(os.environ.get("JIRA_POLL_INTERVAL", _DEFAULT_POLL_INTERVAL))
        self._employee_map: dict[str, str] = json.loads(
            os.environ.get("JIRA_EMPLOYEE_MAP", "{}")
        )
        self._http: httpx.Client | None = None
        self._running = False
        # Watermark: only pull issues updated after this timestamp
        self._since: datetime = datetime.now(tz=timezone.utc) - timedelta(
            minutes=_DEFAULT_LOOKBACK_MINUTES
        )

    # ── BaseProducer contract ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Verify Jira credentials via /rest/api/3/myself."""
        if not all([self._base_url, self._email, self._api_token]):
            raise ValueError("JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN must all be set")

        token = base64.b64encode(
            f"{self._email}:{self._api_token}".encode("utf-8")
        ).decode("utf-8")

        self._http = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        resp = self._http.get("/rest/api/3/myself")
        resp.raise_for_status()
        me = resp.json()
        logger.info("Jira connector: authenticated as accountId=%s", me.get("accountId"))
        ConnectorRegistry.get().set_healthy("jira", healthy=True)

    def stream_events(self) -> Iterator[CollaborationEvent]:
        """Poll Jira for updated issues and emit collaboration edges."""
        self._running = True
        while self._running:
            try:
                yield from self._poll_updated_issues()
                ConnectorRegistry.get().set_healthy("jira", healthy=True)
            except Exception as exc:
                logger.warning("Jira polling error: %s", exc)
                ConnectorRegistry.get().set_healthy("jira", healthy=False, error=str(exc))

            for _ in range(self._poll_interval):
                if not self._running:
                    return
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

    # ── Jira REST API ─────────────────────────────────────────────────────────

    def _poll_updated_issues(self) -> Iterator[CollaborationEvent]:
        """Fetch issues updated since the watermark and emit edges."""
        since_str = self._since.strftime("%Y-%m-%d %H:%M")

        project_clause = ""
        if self._projects:
            keys = ", ".join(f'"{k}"' for k in self._projects)
            project_clause = f"project in ({keys}) AND "

        jql = f'{project_clause}updated >= "{since_str}" ORDER BY updated ASC'

        start_at = 0
        max_results = 100
        poll_start = datetime.now(tz=timezone.utc)

        while True:
            resp = self._http.post(
                "/rest/api/3/issue/search",
                json={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    # Only fetch the fields we need — no description, no summary
                    "fields": ["assignee", "reporter", "comment", "updated"],
                },
            )
            resp.raise_for_status()
            page = resp.json()

            issues = page.get("issues", [])
            for issue in issues:
                yield from self._extract_edges(issue)

            total = page.get("total", 0)
            start_at += len(issues)
            if start_at >= total or not issues:
                break

        # Advance watermark after a successful poll
        self._since = poll_start

    def _extract_edges(self, issue: dict) -> Iterator[CollaborationEvent]:
        """Emit edges from a single Jira issue."""
        fields = issue.get("fields", {})
        updated_str = fields.get("updated", "")
        try:
            ts = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(tz=timezone.utc)

        assignee_id = (fields.get("assignee") or {}).get("accountId")
        reporter_id = (fields.get("reporter") or {}).get("accountId")

        # Edge 1: reporter → assignee (assignment relationship)
        if reporter_id and assignee_id and reporter_id != assignee_id:
            event = self._build_event(reporter_id, assignee_id, "assigned", ts)
            if event:
                ConnectorRegistry.get().record_event("jira")
                yield event

        # Edge 2: each comment author → assignee (mention relationship)
        # We read ONLY the author's accountId — zero comment text is accessed.
        comments_data = (fields.get("comment") or {}).get("comments", [])
        for comment in comments_data:
            author_id = (comment.get("author") or {}).get("accountId")
            if author_id and assignee_id and author_id != assignee_id:
                try:
                    comment_ts = datetime.fromisoformat(
                        comment.get("created", updated_str).replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    comment_ts = ts
                event = self._build_event(author_id, assignee_id, "mentioned", comment_ts)
                if event:
                    ConnectorRegistry.get().record_event("jira")
                    yield event

    def _resolve_employee_id(self, jira_account_id: str) -> str:
        return self._employee_map.get(jira_account_id, jira_account_id)

    def _build_event(
        self,
        source_id: str,
        target_id: str,
        direction: str,
        timestamp: datetime,
    ) -> CollaborationEvent | None:
        if source_id == target_id:
            return None
        return CollaborationEvent(
            event_id=str(uuid.uuid4()),
            source_employee_id=self._resolve_employee_id(source_id),
            target_employee_id=self._resolve_employee_id(target_id),
            channel="jira",
            direction=direction,  # type: ignore[arg-type]
            department_source="unknown",
            department_target="unknown",
            timestamp=timestamp,
            weight=1.0,
        )
