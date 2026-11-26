"""Thin async client for the Organizational Synapse REST API.

The MCP server never touches the database: every tool goes through the same
HTTP API the web app uses, so the API's tenant isolation, role checks
(e.g. hr_admin-only routes), consent filters and k-anonymity guards apply to
an LLM exactly as they do to a person in the dashboard.

Configuration (environment):
    SYNAPSE_API_URL     API base URL                  default http://localhost:8001
    SYNAPSE_APP_URL     web app base URL (deep links) default http://localhost:5173
    SYNAPSE_TENANT_ID   X-Tenant-ID header            multi-tenant deployments only
    SYNAPSE_API_KEY     X-Api-Key header              multi-tenant deployments only
    SYNAPSE_TIMEOUT     request timeout, seconds      default 60
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError


class SynapseAPI:
    """HTTP access to the API with LLM-friendly errors and a short snapshot cache."""

    def __init__(
        self,
        base_url: str | None = None,
        app_url: str | None = None,
        tenant_id: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("SYNAPSE_API_URL", "http://localhost:8001")).rstrip("/")
        self.app_url = (app_url or os.environ.get("SYNAPSE_APP_URL", "http://localhost:5173")).rstrip("/")
        headers = {"Accept": "application/json", "User-Agent": "org-synapse-mcp"}
        tenant_id = tenant_id if tenant_id is not None else os.environ.get("SYNAPSE_TENANT_ID", "")
        api_key = api_key if api_key is not None else os.environ.get("SYNAPSE_API_KEY", "")
        if tenant_id:
            headers["X-Tenant-ID"] = tenant_id
        if api_key:
            headers["X-Api-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout or float(os.environ.get("SYNAPSE_TIMEOUT", "60")),
            transport=transport,
        )
        self._snapshot: tuple[float, dict] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── HTTP ─────────────────────────────────────────────────────────────────

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            resp = await self._client.request(method, path, params=clean, json=json)
        except httpx.ConnectError as exc:
            raise ToolError(
                f"The Organizational Synapse API is not reachable at {self.base_url}. "
                "Start it (e.g. `.\\demo.ps1`, or uvicorn api.main:app --port 8001) or set SYNAPSE_API_URL."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ToolError(f"The API did not answer {method} {path} in time.") from exc

        if resp.status_code >= 400:
            raise ToolError(_error_message(resp, method, path))
        if not resp.content:
            return {}
        ctype = resp.headers.get("content-type", "")
        return resp.json() if "json" in ctype else resp.text

    async def get(self, path: str, **params) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: Any = None, **params) -> Any:
        return await self.request("POST", path, params=params, json=body)

    async def optional(self, path: str, **params) -> Any | None:
        """GET that returns None on 404 (data not computed yet) instead of failing."""
        try:
            return await self.get(path, **params)
        except ToolError as exc:
            if str(exc).startswith("Not found"):
                return None
            raise

    # ── Shared lookups ───────────────────────────────────────────────────────

    async def snapshot(self, max_age: float = 60.0) -> dict:
        """Latest graph snapshot (≈ 200 KB), cached briefly: many tools need names."""
        now = time.monotonic()
        if self._snapshot and now - self._snapshot[0] < max_age:
            return self._snapshot[1]
        data = await self.get("/graph/snapshot")
        self._snapshot = (now, data)
        return data

    async def names(self) -> dict[str, dict]:
        """employee_id → {name, department} from the latest snapshot."""
        snap = await self.snapshot()
        return {
            n["employee_id"]: {"name": n.get("name"), "department": n.get("department")} for n in snap.get("nodes", [])
        }

    def employee_link(self, employee_id: str) -> str:
        return f"{self.app_url}/employee/{employee_id}"


def _error_message(resp: httpx.Response, method: str, path: str) -> str:
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}" for d in detail)
    if resp.status_code == 404:
        return f"Not found: {detail}"
    if resp.status_code in (401, 403):
        return (
            f"Not authorised ({resp.status_code}): {detail}. The API key configured for this MCP server "
            "(SYNAPSE_API_KEY) does not have access to this data."
        )
    return f"API error {resp.status_code} on {method} {path}: {detail}"
