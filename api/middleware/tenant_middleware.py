"""Lightweight tenant header extraction middleware (F6).

Copies X-Tenant-ID and X-Api-Key headers onto request.state so that any
downstream code (dependencies, route handlers) can read them without
importing fastapi.Request directly.

This middleware performs NO database validation — that is deferred to the
get_tenant_db() dependency so that routes exempt from multi-tenancy
(health, admin, webhook, docs) never pay the DB validation cost.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TenantMiddleware(BaseHTTPMiddleware):
    """Extract tenant headers and attach them to request.state."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.tenant_id = request.headers.get("X-Tenant-ID", "")
        request.state.api_key   = request.headers.get("X-Api-Key", "")
        return await call_next(request)
