"""Router: /admin — tenant management and API key administration (F6).

All endpoints require X-Admin-Key header matching ADMIN_SECRET_KEY env var.
Only accessible from internal networks in production deployments.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_admin_db
from api.models.schemas import (
    TenantCreateRequest,
    TenantCreateResponse,
    TenantDetail,
    TenantListResponse,
    TenantApiKeyResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


# ─── Tenant CRUD ──────────────────────────────────────────────────────────────


@router.post("/tenants", response_model=TenantCreateResponse, status_code=201)
def create_tenant(
    body: TenantCreateRequest,
    conn=Depends(get_admin_db),
) -> TenantCreateResponse:
    """Provision a new tenant: creates schema, tables, and a default API key.

    The raw_api_key in the response is shown exactly once — it cannot be
    retrieved again.  Store it securely immediately.
    """
    from api.tenant import provision_tenant_schema

    try:
        result = provision_tenant_schema(
            slug=body.slug,
            name=body.name,
            plan=body.plan,
            conn=conn,
        )
    except Exception as exc:
        logger.exception("Tenant provisioning failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not provision tenant: {exc}",
        ) from exc

    return TenantCreateResponse(**result)


@router.get("/tenants", response_model=TenantListResponse)
def list_tenants(
    include_inactive: bool = False,
    conn=Depends(get_admin_db),
) -> TenantListResponse:
    """List all tenants in the registry."""
    with conn.cursor() as cur:
        if include_inactive:
            cur.execute(
                """
                SELECT id::text, slug, name, plan, schema_name, active,
                       stripe_customer_id, created_at
                FROM public.tenants
                ORDER BY created_at DESC
                """
            )
        else:
            cur.execute(
                """
                SELECT id::text, slug, name, plan, schema_name, active,
                       stripe_customer_id, created_at
                FROM public.tenants
                WHERE active = true
                ORDER BY created_at DESC
                """
            )
        rows = [dict(r) for r in cur.fetchall()]

    tenants = [TenantDetail(**r) for r in rows]
    return TenantListResponse(total=len(tenants), tenants=tenants)


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
def get_tenant(tenant_id: str, conn=Depends(get_admin_db)) -> TenantDetail:
    """Return detail for one tenant."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, slug, name, plan, schema_name, active,
                   stripe_customer_id, created_at
            FROM public.tenants
            WHERE id = %s::uuid
            """,
            (tenant_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found.")
    return TenantDetail(**dict(row))


@router.patch("/tenants/{tenant_id}/plan")
def update_tenant_plan(
    tenant_id: str,
    plan: str,
    conn=Depends(get_admin_db),
) -> dict:
    """Change a tenant's plan (free → starter → pro → enterprise)."""
    valid_plans = {"free", "starter", "pro", "enterprise"}
    if plan not in valid_plans:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid plan. Must be one of: {sorted(valid_plans)}",
        )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.tenants
               SET plan = %s, updated_at = NOW()
             WHERE id = %s::uuid
            RETURNING id::text
            """,
            (plan, tenant_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found.")
    return {"tenant_id": tenant_id, "plan": plan}


@router.delete("/tenants/{tenant_id}", status_code=204)
def deactivate_tenant(
    tenant_id: str,
    drop_schema: bool = False,
    conn=Depends(get_admin_db),
) -> None:
    """Soft-delete a tenant (active = false).

    Pass ?drop_schema=true to also DROP the tenant schema — irreversible.
    """
    from api.tenant import deprovision_tenant

    deprovision_tenant(tenant_id, conn, drop_schema=drop_schema)
    logger.warning(
        "Tenant %s deactivated (drop_schema=%s)", tenant_id, drop_schema
    )


# ─── API key management ───────────────────────────────────────────────────────


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="default", max_length=100)


@router.post("/tenants/{tenant_id}/api-keys", response_model=TenantApiKeyResponse, status_code=201)
def create_api_key(
    tenant_id: str,
    body: ApiKeyCreateRequest,
    conn=Depends(get_admin_db),
) -> TenantApiKeyResponse:
    """Create a new API key for a tenant.

    Returns the raw key once — it cannot be retrieved again.
    """
    from api.tenant import generate_api_key

    # Verify tenant exists
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.tenants WHERE id = %s::uuid AND active = true",
            (tenant_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found.")

    raw_key, key_hash = generate_api_key()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.tenant_api_keys (tenant_id, key_hash, name)
            VALUES (%s::uuid, %s, %s)
            RETURNING id::text, created_at
            """,
            (tenant_id, key_hash, body.name),
        )
        row = cur.fetchone()

    return TenantApiKeyResponse(
        key_id=row["id"],
        tenant_id=tenant_id,
        name=body.name,
        raw_api_key=raw_key,
        created_at=row["created_at"],
    )


@router.delete("/tenants/{tenant_id}/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    tenant_id: str,
    key_id: str,
    conn=Depends(get_admin_db),
) -> None:
    """Revoke (soft-delete) a tenant API key."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.tenant_api_keys
               SET active = false
             WHERE id = %s::uuid AND tenant_id = %s::uuid
            RETURNING id
            """,
            (key_id, tenant_id),
        )
        if not cur.fetchone():
            raise HTTPException(
                status_code=404,
                detail=f"API key {key_id} not found for tenant {tenant_id}.",
            )


# ─── Usage overview (admin view across all tenants) ───────────────────────────


@router.get("/usage")
def get_all_usage(
    month: date | None = None,
    conn=Depends(get_admin_db),
) -> dict:
    """Return event usage across all tenants for the given month (default: current)."""
    with conn.cursor() as cur:
        if month:
            cur.execute(
                """
                SELECT t.slug, t.name, t.plan,
                       COALESCE(u.event_count, 0) AS event_count,
                       u.month
                FROM public.tenants t
                LEFT JOIN public.tenant_usage u
                    ON u.tenant_id = t.id AND u.month = %s
                WHERE t.active = true
                ORDER BY event_count DESC
                """,
                (month,),
            )
        else:
            cur.execute(
                """
                SELECT t.slug, t.name, t.plan,
                       COALESCE(u.event_count, 0) AS event_count,
                       u.month
                FROM public.tenants t
                LEFT JOIN public.tenant_usage u
                    ON u.tenant_id = t.id
                   AND u.month = DATE_TRUNC('month', NOW())::date
                WHERE t.active = true
                ORDER BY event_count DESC
                """
            )
        rows = [dict(r) for r in cur.fetchall()]

    return {"total_tenants": len(rows), "tenants": rows}


# ─── Dev / demo tools ─────────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
_SCRIPT_TIMEOUT = 180  # seconds


def _require_admin(
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
) -> None:
    """Auth-only variant of get_admin_db (no DB connection needed)."""
    secret = os.environ.get("ADMIN_SECRET_KEY", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is disabled — set ADMIN_SECRET_KEY to enable.",
        )
    if not x_admin_key or x_admin_key != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Admin-Key header.",
        )


async def _run_script(args: list[str]) -> str:
    """Run a Python script in a thread-pool executor and return its combined output.

    Uses subprocess.run (blocking) inside run_in_executor so the event loop is
    not blocked and asyncio.create_subprocess_exec Windows compatibility is not
    a factor.
    """
    loop = asyncio.get_running_loop()

    def _blocking() -> tuple[int, str]:
        result = subprocess.run(
            [sys.executable, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_SCRIPT_TIMEOUT,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace")

    try:
        returncode, output = await asyncio.wait_for(
            loop.run_in_executor(None, _blocking),
            timeout=_SCRIPT_TIMEOUT + 10,
        )
    except (asyncio.TimeoutError, subprocess.TimeoutExpired):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Script timed out after {_SCRIPT_TIMEOUT}s.",
        )

    if returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=output or "Script exited with non-zero status.",
        )
    return output


@router.post("/dev/seed")
async def dev_seed(
    employees: int = 120,
    days: int = 60,
    _: None = Depends(_require_admin),
) -> dict:
    """Generate synthetic employees, events, and graph data.

    Runs scripts/seed_dev.py in a subprocess. For local development and demo
    use only — do not expose in production.
    """
    script = str(_SCRIPTS_DIR / "seed_dev.py")
    output = await _run_script([script, "--employees", str(employees), "--days", str(days)])
    logger.info("dev/seed completed: employees=%d days=%d", employees, days)
    return {"ok": True, "employees": employees, "days": days, "output": output}


@router.post("/dev/reset")
async def dev_reset(
    _: None = Depends(_require_admin),
) -> dict:
    """Truncate all 20 application tables (cascade).

    Runs scripts/reset_db.py --yes in a subprocess. For local development and
    demo use only — do not expose in production.
    """
    script = str(_SCRIPTS_DIR / "reset_db.py")
    output = await _run_script([script, "--yes"])
    logger.warning("dev/reset executed — all application data cleared")
    return {"ok": True, "output": output}
