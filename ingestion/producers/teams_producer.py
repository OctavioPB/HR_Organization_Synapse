"""Microsoft Teams metadata connector via Microsoft Graph API.

Privacy model:
  - Only captures: who attended a meeting with whom (no transcript, no content).
  - Uses CallRecords API: /communications/callRecords
  - Required Graph API permissions (application): CallRecords.Read.All
  - NO access to chat messages, file content, or email bodies.

The connector polls the Graph API on a configurable interval. Microsoft does
not support streaming for CallRecords; this is inherently pull-based.

Environment variables:
    TEAMS_TENANT_ID      Azure AD tenant ID
    TEAMS_CLIENT_ID      Azure AD application (client) ID
    TEAMS_CLIENT_SECRET  Azure AD client secret
    TEAMS_POLL_INTERVAL  Seconds between polling cycles (default: 600)
    TEAMS_LOOKBACK_DAYS  Days of call records to fetch on first run (default: 1)
    TEAMS_EMPLOYEE_MAP   JSON dict mapping AAD object ID → employee UUID (optional)
"""

from __future__ import annotations

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

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_DEFAULT_POLL_INTERVAL = 600  # seconds
_DEFAULT_LOOKBACK_DAYS = 1


class TeamsProducer(BaseProducer):
    """Microsoft Teams connector — polls Graph API for meeting metadata.

    Emits one CollaborationEvent per ordered participant pair per call record.
    For a meeting with N participants, this produces N*(N-1) directed edges,
    one for each (organizer/attendee → co-attendee) pair.
    """

    channel = "teams"

    def __init__(self) -> None:
        self._tenant_id: str = os.environ.get("TEAMS_TENANT_ID", "")
        self._client_id: str = os.environ.get("TEAMS_CLIENT_ID", "")
        self._client_secret: str = os.environ.get("TEAMS_CLIENT_SECRET", "")
        self._poll_interval: int = int(os.environ.get("TEAMS_POLL_INTERVAL", _DEFAULT_POLL_INTERVAL))
        self._lookback_days: int = int(os.environ.get("TEAMS_LOOKBACK_DAYS", _DEFAULT_LOOKBACK_DAYS))
        self._employee_map: dict[str, str] = json.loads(
            os.environ.get("TEAMS_EMPLOYEE_MAP", "{}")
        )
        self._http: httpx.Client | None = None
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._running = False
        self._last_call_end_time: datetime = datetime.now(tz=timezone.utc) - timedelta(
            days=self._lookback_days
        )

    # ── BaseProducer contract ─────────────────────────────────────────────────

    def connect(self) -> None:
        """Obtain an OAuth2 client-credentials token from Azure AD."""
        if not all([self._tenant_id, self._client_id, self._client_secret]):
            raise ValueError(
                "TEAMS_TENANT_ID, TEAMS_CLIENT_ID, and TEAMS_CLIENT_SECRET must all be set"
            )
        self._http = httpx.Client(timeout=15.0)
        self._refresh_token()
        ConnectorRegistry.get().set_healthy("teams", healthy=True)
        logger.info("Teams connector: OAuth token acquired")

    def stream_events(self) -> Iterator[CollaborationEvent]:
        """Poll Graph API for new call records and emit participant-pair edges."""
        self._running = True
        while self._running:
            try:
                yield from self._fetch_call_records()
                ConnectorRegistry.get().set_healthy("teams", healthy=True)
            except Exception as exc:
                logger.warning("Teams polling error: %s", exc)
                ConnectorRegistry.get().set_healthy("teams", healthy=False, error=str(exc))

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

    # ── OAuth token management ────────────────────────────────────────────────

    def _refresh_token(self) -> None:
        """Fetch a new client-credentials access token from Azure AD."""
        token_url = _TOKEN_URL.format(tenant_id=self._tenant_id)
        resp = self._http.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        token_data = resp.json()
        if "error" in token_data:
            raise ConnectionError(f"Azure AD token error: {token_data['error_description']}")
        self._access_token = token_data["access_token"]
        self._token_expires_at = time.monotonic() + token_data.get("expires_in", 3599) - 60
        self._http.headers["Authorization"] = f"Bearer {self._access_token}"

    def _ensure_token_valid(self) -> None:
        if time.monotonic() >= self._token_expires_at:
            logger.debug("Teams: refreshing expired OAuth token")
            self._refresh_token()

    # ── Graph API fetching ────────────────────────────────────────────────────

    def _fetch_call_records(self) -> Iterator[CollaborationEvent]:
        """Retrieve call records created after _last_call_end_time and emit edges."""
        self._ensure_token_valid()
        since = self._last_call_end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (
            f"{_GRAPH_BASE}/communications/callRecords"
            f"?$filter=endDateTime ge {since}"
            f"&$expand=sessions($expand=segments)"
            f"&$top=50"
        )

        while url:
            resp = self._http.get(url)
            resp.raise_for_status()
            page = resp.json()

            for record in page.get("value", []):
                yield from self._process_call_record(record)
                # Advance the watermark to avoid re-processing
                end_dt = datetime.fromisoformat(
                    record["endDateTime"].replace("Z", "+00:00")
                )
                if end_dt > self._last_call_end_time:
                    self._last_call_end_time = end_dt

            url = page.get("@odata.nextLink")

    def _process_call_record(self, record: dict) -> Iterator[CollaborationEvent]:
        """Extract participant pairs from a call record and yield collaboration edges."""
        participants: list[str] = []

        # Collect all unique participant identities from sessions/segments
        for session in record.get("sessions", []):
            for role in ("caller", "callee"):
                identity = (
                    session
                    .get(role, {})
                    .get("identity", {})
                    .get("user", {})
                    .get("id")
                )
                if identity and identity not in participants:
                    participants.append(identity)
            for segment in session.get("segments", []):
                for role in ("caller", "callee"):
                    identity = (
                        segment
                        .get(role, {})
                        .get("identity", {})
                        .get("user", {})
                        .get("id")
                    )
                    if identity and identity not in participants:
                        participants.append(identity)

        call_ts = datetime.fromisoformat(
            record.get("startDateTime", datetime.now(tz=timezone.utc).isoformat())
            .replace("Z", "+00:00")
        )

        # Emit one directed edge per ordered pair
        for i, source_aad_id in enumerate(participants):
            for target_aad_id in participants[i + 1:]:
                event = self._build_event(source_aad_id, target_aad_id, call_ts)
                if event:
                    ConnectorRegistry.get().record_event("teams")
                    yield event

    def _resolve_employee_id(self, aad_object_id: str) -> str:
        return self._employee_map.get(aad_object_id, aad_object_id)

    def _build_event(
        self,
        source_aad_id: str,
        target_aad_id: str,
        timestamp: datetime,
    ) -> CollaborationEvent | None:
        if source_aad_id == target_aad_id:
            return None
        return CollaborationEvent(
            event_id=str(uuid.uuid4()),
            source_employee_id=self._resolve_employee_id(source_aad_id),
            target_employee_id=self._resolve_employee_id(target_aad_id),
            channel="teams",
            direction="invited",
            department_source="unknown",
            department_target="unknown",
            timestamp=timestamp,
            weight=1.0,
        )
