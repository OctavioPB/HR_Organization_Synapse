"""Confluence knowledge metadata connector — batch ETL.

Privacy model:
  - Captures: who authored/edited which page and in which space/topic.
  - NO page body, NO page content, NO comments, NO attachments.
  - Required Confluence permissions: read-only (space:read, page:read).

This connector fetches document metadata from Confluence Cloud REST API v2
and writes it to the document_knowledge table.  It is a batch ETL connector
(not a Kafka streaming producer) — designed to run from an Airflow task.

Environment variables:
    CONFLUENCE_BASE_URL      e.g. https://yourorg.atlassian.net
    CONFLUENCE_EMAIL         Service account email (Basic Auth)
    CONFLUENCE_API_TOKEN     Confluence API token
    CONFLUENCE_SPACES        Comma-separated space keys to monitor (e.g. ENG,HR,OPS)
                             Empty = all spaces
    CONFLUENCE_LOOKBACK_DAYS Days of modified pages to ingest (default: 7)
    CONFLUENCE_EMPLOYEE_MAP  JSON dict: {"confluence-account-id": "employee-uuid", ...}
    ENABLE_CONFLUENCE        Set to "true" to activate (default: false)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Iterator
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS  = int(os.environ.get("CONFLUENCE_LOOKBACK_DAYS", "7"))
_PAGE_SIZE      = 100
_TIMEOUT_SEC    = 30


class ConfluenceConnector:
    """Batch ETL: ingests Confluence page metadata into document_knowledge.

    Only page metadata is captured — titles, space keys, labels, and the
    account IDs of authors and contributors.  Page bodies are never fetched
    (we call the metadata-only endpoint without body expansion).
    """

    source: str = "confluence"

    def __init__(self) -> None:
        self._base_url    = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
        self._email       = os.environ.get("CONFLUENCE_EMAIL", "")
        self._api_token   = os.environ.get("CONFLUENCE_API_TOKEN", "")
        self._spaces      = [
            s.strip()
            for s in os.environ.get("CONFLUENCE_SPACES", "").split(",")
            if s.strip()
        ]
        self._employee_map: dict[str, str] = json.loads(
            os.environ.get("CONFLUENCE_EMPLOYEE_MAP", "{}")
        )
        self._lookback_days = int(os.environ.get("CONFLUENCE_LOOKBACK_DAYS", str(_LOOKBACK_DAYS)))

    # ── Connectivity ──────────────────────────────────────────────────────

    def _headers(self) -> dict:
        import base64
        credentials = base64.b64encode(
            f"{self._email}:{self._api_token}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        }

    def health_check(self) -> dict:
        """Verify API credentials are valid."""
        if not (self._base_url and self._email and self._api_token):
            return {"healthy": False, "error": "missing_credentials"}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    f"{self._base_url}/wiki/api/v2/spaces?limit=1",
                    headers=self._headers(),
                )
                resp.raise_for_status()
            return {"healthy": True}
        except Exception as exc:
            return {"healthy": False, "error": str(exc)}

    # ── Document fetching ─────────────────────────────────────────────────

    def _fetch_pages(self) -> Iterator[dict]:
        """Yield raw page dicts from Confluence REST API v2 (metadata only).

        Fetches pages modified in the last lookback_days.  Paginates via
        the cursor returned in each response.
        """
        since = (
            datetime.now(tz=timezone.utc) - timedelta(days=self._lookback_days)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        params: dict = {
            "limit": _PAGE_SIZE,
            "sort": "modified-date",
            # Request only the fields we need — never request body
            "body-format": "none",
        }
        if self._spaces:
            params["space-key"] = ",".join(self._spaces)

        cursor: str | None = None

        with httpx.Client(
            base_url=f"{self._base_url}/wiki/api/v2",
            headers=self._headers(),
            timeout=_TIMEOUT_SEC,
        ) as client:
            while True:
                if cursor:
                    params["cursor"] = cursor

                resp = client.get("/pages", params=params)
                resp.raise_for_status()
                data = resp.json()
                pages = data.get("results", [])

                for page in pages:
                    # Filter out pages older than lookback window
                    modified_str = (
                        page.get("version", {}).get("createdAt")
                        or page.get("createdAt", "")
                    )
                    if modified_str < since:
                        return
                    yield page

                # Follow pagination cursor
                next_link = data.get("_links", {}).get("next")
                if not next_link or not pages:
                    break
                # Extract cursor from next link query string
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(next_link).query)
                cursor = qs.get("cursor", [None])[0]
                if not cursor:
                    break

    def _fetch_page_labels(self, client: httpx.Client, page_id: str) -> list[str]:
        """Return label values for a page (used as additional domain tags)."""
        try:
            resp = client.get(f"/pages/{page_id}/labels", params={"limit": 50})
            resp.raise_for_status()
            return [lbl["name"] for lbl in resp.json().get("results", [])]
        except Exception:
            return []

    def _extract_domains(self, page: dict, labels: list[str]) -> list[str]:
        """Derive knowledge domain tags from space key + page labels."""
        domains: list[str] = []
        space_key = page.get("spaceId", "")
        if space_key:
            domains.append(space_key.lower().replace("_", "-"))
        for label in labels:
            tag = label.strip().lower().replace(" ", "-")
            if tag and len(tag) <= 60:
                domains.append(tag)
        return list(dict.fromkeys(domains))  # deduplicate, preserve order

    def _map_user(self, account_id: str | None) -> str | None:
        """Map a Confluence account ID to an employee UUID (or None)."""
        if not account_id:
            return None
        return self._employee_map.get(account_id)

    def _extract_contributors(self, page: dict) -> list[str]:
        """Extract all distinct contributor employee UUIDs from a page."""
        contributors: set[str] = set()
        # Author
        author_acc = (
            page.get("version", {}).get("authorId")
            or page.get("authorId")
        )
        emp = self._map_user(author_acc)
        if emp:
            contributors.add(emp)
        # Last modifier (may differ from original author)
        modifier_acc = page.get("version", {}).get("authorId")
        emp2 = self._map_user(modifier_acc)
        if emp2:
            contributors.add(emp2)
        return list(contributors)

    # ── Ingestion ──────────────────────────────────────────────────────────

    def ingest(self, conn) -> int:
        """Fetch pages from Confluence and upsert into document_knowledge.

        Args:
            conn: Open psycopg2 connection.

        Returns:
            Number of pages upserted.
        """
        if not (self._base_url and self._email and self._api_token):
            logger.warning(
                "ConfluenceConnector: missing credentials — skipping ingestion. "
                "Set CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN."
            )
            return 0

        upserted = 0
        with httpx.Client(
            base_url=f"{self._base_url}/wiki/api/v2",
            headers=self._headers(),
            timeout=_TIMEOUT_SEC,
        ) as client:
            for page in self._fetch_pages():
                page_id = page.get("id", "")
                title   = page.get("title", "")
                labels  = self._fetch_page_labels(client, page_id)
                domains = self._extract_domains(page, labels)

                if not domains:
                    continue  # skip untagged pages — no domain signal

                author_acc = (
                    page.get("version", {}).get("authorId")
                    or page.get("authorId")
                )
                author_emp = self._map_user(author_acc)
                contributors = self._extract_contributors(page)
                # Ensure all unique contributors (including author)
                if author_emp and author_emp not in contributors:
                    contributors.append(author_emp)

                modified_str = (
                    page.get("version", {}).get("createdAt")
                    or page.get("createdAt", "")
                ) or datetime.now(tz=timezone.utc).isoformat()

                try:
                    modified_at = datetime.fromisoformat(
                        modified_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    modified_at = datetime.now(tz=timezone.utc)

                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO document_knowledge
                                (source, doc_id, title, author_id,
                                 contributor_ids, domain_tags, last_modified_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (source, doc_id) DO UPDATE
                                SET title            = EXCLUDED.title,
                                    author_id        = EXCLUDED.author_id,
                                    contributor_ids  = EXCLUDED.contributor_ids,
                                    domain_tags      = EXCLUDED.domain_tags,
                                    last_modified_at = EXCLUDED.last_modified_at,
                                    ingested_at      = NOW()
                            """,
                            (
                                self.source,
                                page_id,
                                title,
                                author_emp,
                                contributors or [],
                                domains,
                                modified_at,
                            ),
                        )
                    conn.commit()
                    upserted += 1
                except Exception as exc:
                    logger.error(
                        "Failed to upsert page %s: %s", page_id[:16], exc
                    )
                    conn.rollback()

        logger.info(
            "ConfluenceConnector: ingested %d pages (lookback=%dd)",
            upserted, self._lookback_days,
        )
        return upserted
