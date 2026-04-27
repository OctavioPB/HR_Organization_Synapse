"""FastAPI dependency providers.

Single-tenant (legacy):
    get_db()        — psycopg2 connection to the public schema (existing routes)

Multi-tenant (F6):
    get_tenant_db() — validates X-Tenant-ID + X-Api-Key, sets search_path to
                      tenant schema, yields a scoped connection
    get_admin_db()  — validates X-Admin-Key, yields a public-schema connection
                      for admin routes only
"""

from __future__ import annotations

import os
from typing import Generator

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from fastapi import Depends, Header, HTTPException, Request, status


# ─── Connection factory ───────────────────────────────────────────────────────


def _open_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "org_synapse"),
        user=os.environ.get("POSTGRES_USER", "opb"),
        password=os.environ.get("POSTGRES_PASSWORD", "changeme"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


# ─── Single-tenant dependency (existing routes — no change) ───────────────────


def get_db() -> Generator[psycopg2.extensions.connection, None, None]:
    """Yield a psycopg2 connection against the public schema.

    Existing routes use this dependency unchanged.  Multi-tenant routes
    use get_tenant_db() instead.
    """
    conn = _open_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Multi-tenant dependency (F6) ────────────────────────────────────────────


def get_tenant_db(
    request: Request,
) -> Generator[psycopg2.extensions.connection, None, None]:
    """Validate tenant credentials and yield a tenant-scoped DB connection.

    1. Reads X-Tenant-ID and X-Api-Key from request.state (set by TenantMiddleware).
    2. Looks up the tenant and validates the API key (SHA-256 hash comparison).
    3. Sets search_path to the tenant's schema for the duration of the request.
    4. Attaches the resolved TenantContext to request.state.tenant.

    Raises 401 on missing or invalid credentials.
    Raises 403 if the tenant account is inactive.
    """
    from api.tenant import resolve_tenant, set_search_path

    tenant_id = getattr(request.state, "tenant_id", "") or ""
    api_key   = getattr(request.state, "api_key", "") or ""

    if not tenant_id or not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Tenant-ID or X-Api-Key header.",
        )

    conn = _open_connection()
    try:
        tenant = resolve_tenant(tenant_id, api_key, conn)
        if tenant is None:
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid tenant credentials.",
            )
        if not tenant.active:
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant account is inactive.",
            )

        set_search_path(conn, tenant.schema_name)
        request.state.tenant = tenant

        yield conn
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        raise
    finally:
        if not conn.closed:
            conn.close()


# ─── Admin dependency (F6) ───────────────────────────────────────────────────


_ADMIN_SECRET = os.environ.get("ADMIN_SECRET_KEY", "")


def get_admin_db(
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
) -> Generator[psycopg2.extensions.connection, None, None]:
    """Validate the admin key and yield a public-schema DB connection.

    Admin routes manage tenants and billing data in the public schema.
    Protect with ADMIN_SECRET_KEY env var.  If ADMIN_SECRET_KEY is not set,
    the admin endpoints are disabled (returns 503).
    """
    if not _ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is disabled — set ADMIN_SECRET_KEY to enable.",
        )

    # Reload at call time so env var changes take effect without restart
    secret = os.environ.get("ADMIN_SECRET_KEY", "")
    if not x_admin_key or x_admin_key != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Admin-Key header.",
        )

    conn = _open_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
