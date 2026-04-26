"""Notion knowledge metadata connector — batch ETL.

Privacy model:
  - Captures: who created/last-edited which page and in which database/workspace.
  - NO page content, NO block text, NO comment bodies, NO file URLs.
  - Required Notion permissions: read-only integration with page_read capability.

This connector uses the Notion REST API to fetch page metadata and writes it
to the document_knowledge table.  It is a batch ETL connector designed to run
from an Airflow task.

Environment variables:
    NOTION_API_TOKEN        Notion integration token (secret_...)
    NOTION_LOOKBACK_DAYS    Days of edited pages to ingest (default: 7)
    NOTION_EMPLOYEE_MAP     JSON dict: {"notion-user-id": "employee-uuid", ...}
    ENABLE_NOTION           Set to "true" to activate (default: false)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = int(os.environ.get("NOTION_LOOKBACK_DAYS", "7"))
_PAGE_SIZE     = 100
_TIMEOUT_SEC   = 30
_API_VERSION   = "2022-06-28"


class NotionConnector:
    """Batch ETL: ingests Notion page metadata into document_knowledge.

    Only page metadata is captured: page ID, title (from title property),
    created_by, last_edited_by, parent database/page, and explicit tag
    properties.  The page content (blocks) is never fetched.
    """

    source: str = "notion"

    def __init__(self) -> None:
        self._api_token  = os.environ.get("NOTION_API_TOKEN", "")
        self._lookback_days = int(os.environ.get("NOTION_LOOKBACK_DAYS", str(_LOOKBACK_DAYS)))
        self._employee_map: dict[str, str] = json.loads(
            os.environ.get("NOTION_EMPLOYEE_MAP", "{}")
        )

    # ── Connectivity ──────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Notion-Version": _API_VERSION,
            "Content-Type": "application/json",
        }

    def health_check(self) -> dict:
        """Verify API token is valid by fetching the integration's own user."""
        if not self._api_token:
            return {"healthy": False, "error": "missing_api_token"}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    "https://api.notion.com/v1/users/me",
                    headers=self._headers(),
                )
                resp.raise_for_status()
            return {"healthy": True}
        except Exception as exc:
            return {"healthy": False, "error": str(exc)}

    # ── Document fetching ─────────────────────────────────────────────────

    def _fetch_pages(self) -> Iterator[dict]:
        """Yield page dicts modified in the last lookback_days via /v1/search.

        Uses POST /v1/search with filter: {property: "object", value: "page"}
        and sorts by last_edited_time descending.
        """
        since = (
            datetime.now(tz=timezone.utc) - timedelta(days=self._lookback_days)
        ).isoformat()

        cursor: str | None = None

        with httpx.Client(
            headers=self._headers(),
            timeout=_TIMEOUT_SEC,
        ) as client:
            while True:
                body: dict = {
                    "filter": {"property": "object", "value": "page"},
                    "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                    "page_size": _PAGE_SIZE,
                }
                if cursor:
                    body["start_cursor"] = cursor

                resp = client.post(
                    "https://api.notion.com/v1/search",
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                pages = data.get("results", [])

                for page in pages:
                    last_edited = page.get("last_edited_time", "")
                    if last_edited and last_edited < since:
                        return
                    yield page

                if not data.get("has_more") or not pages:
                    break
                cursor = data.get("next_cursor")
                if not cursor:
                    break

    def _extract_title(self, page: dict) -> str:
        """Extract page title from properties."""
        props = page.get("properties", {})
        for prop_name in ("Name", "Title", "title", "name"):
            prop = props.get(prop_name, {})
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                if title_parts:
                    return "".join(
                        t.get("plain_text", "") for t in title_parts
                    )
        return ""

    def _extract_domains(self, page: dict) -> list[str]:
        """Derive domain tags from parent database name and tag properties.

        Looks for a property named 'Tags', 'Tag', 'Category', or 'Domain'
        of type 'multi_select' or 'select'.
        """
        domains: list[str] = []

        # Parent database ID as a generic domain fallback
        parent = page.get("parent", {})
        if parent.get("type") == "database_id":
            db_id = parent.get("database_id", "").replace("-", "")
            if db_id:
                # Use first 8 chars of db ID as stable short key
                domains.append(f"notion-db-{db_id[:8]}")

        # Explicit tag properties
        props = page.get("properties", {})
        for prop_name in ("Tags", "Tag", "Category", "Domain", "Area", "Team"):
            prop = props.get(prop_name)
            if not prop:
                continue
            ptype = prop.get("type")
            if ptype == "multi_select":
                for opt in prop.get("multi_select", []):
                    tag = opt.get("name", "").strip().lower().replace(" ", "-")
                    if tag and len(tag) <= 60:
                        domains.append(tag)
            elif ptype == "select":
                opt = prop.get("select") or {}
                tag = opt.get("name", "").strip().lower().replace(" ", "-")
                if tag and len(tag) <= 60:
                    domains.append(tag)

        return list(dict.fromkeys(domains))  # deduplicate

    def _map_user(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        return self._employee_map.get(user_id)

    # ── Ingestion ──────────────────────────────────────────────────────────

    def ingest(self, conn) -> int:
        """Fetch pages from Notion and upsert into document_knowledge.

        Args:
            conn: Open psycopg2 connection.

        Returns:
            Number of pages upserted.
        """
        if not self._api_token:
            logger.warning(
                "NotionConnector: missing NOTION_API_TOKEN — skipping ingestion."
            )
            return 0

        upserted = 0
        for page in self._fetch_pages():
            page_id = page.get("id", "")
            title   = self._extract_title(page)
            domains = self._extract_domains(page)

            if not domains:
                continue  # skip untagged pages

            created_by_id    = (page.get("created_by") or {}).get("id")
            last_edited_id   = (page.get("last_edited_by") or {}).get("id")

            author_emp   = self._map_user(created_by_id)
            modifier_emp = self._map_user(last_edited_id)

            contributors: list[str] = list(
                {e for e in [author_emp, modifier_emp] if e}
            )

            modified_str = page.get("last_edited_time", "")
            try:
                modified_at = datetime.fromisoformat(
                    modified_str.replace("Z", "+00:00")
                ) if modified_str else datetime.now(tz=timezone.utc)
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
                            contributors,
                            domains,
                            modified_at,
                        ),
                    )
                conn.commit()
                upserted += 1
            except Exception as exc:
                logger.error("Failed to upsert page %s: %s", page_id[:16], exc)
                conn.rollback()

        logger.info(
            "NotionConnector: ingested %d pages (lookback=%dd)",
            upserted, self._lookback_days,
        )
        return upserted
