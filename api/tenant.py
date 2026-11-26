"""Multi-tenant core — schema resolution, provisioning, and API key validation (F6).

Design: schema-per-tenant isolation in PostgreSQL.
  - public schema: tenant registry, API keys, usage tracking (shared)
  - tenant_{slug} schema: per-tenant copy of all application tables
  - search_path switched at connection time — all subsequent queries go to the
    tenant schema without any code change in the business logic layer.

Public API:
    resolve_tenant(tenant_id, api_key, conn) -> TenantContext | None
    set_search_path(conn, schema_name) -> None
    provision_tenant_schema(slug, name, plan, conn) -> dict
    apply_tenant_migrations(conn, schema_name) -> list[str]
    record_usage(tenant_id, event_count, conn) -> None
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Plan limits ──────────────────────────────────────────────────────────────

PLAN_LIMITS: dict[str, dict] = {
    "free": {"events_per_month": 10_000, "employees_max": 50},
    "starter": {"events_per_month": 100_000, "employees_max": 200},
    "pro": {"events_per_month": 1_000_000, "employees_max": 1_000},
    "enterprise": {"events_per_month": None, "employees_max": None},
}

# ─── Per-tenant schema DDL ────────────────────────────────────────────────────
# Tenant schemas are built from the same ordered migration files as the default
# schema, so a new migration automatically reaches every new tenant.
#
# History: this used to be an inline copy of the DDL (_TENANT_TABLES_SQL). It
# silently drifted — it never received migrations 009–017, so freshly
# provisioned tenants lacked the v2 SPOF band columns and every feature table
# added after 008.  Worse, the request-time search_path is "tenant, public", so
# queries against a missing tenant table fell through to the *public* table.
# Do not reintroduce an inline copy.

_MIGRATIONS_DIR = Path(
    os.environ.get("TENANT_MIGRATIONS_DIR", Path(__file__).resolve().parents[1] / "data" / "migrations")
)

# Migrations that only touch shared, non-tenant objects and must not run per
# tenant: 000 creates the Airflow database (psql \gexec, not executable via a
# driver); 008 creates the public.* tenant registry.  010 also adds a column to
# public.tenant_api_keys, but it is idempotent (ADD COLUMN IF NOT EXISTS) and
# its employees.manager_id change is tenant-scoped, so it stays in the set.
_CONTROL_PLANE_MIGRATIONS = frozenset({"000_create_airflow_db.sql", "008_tenant_schema.sql"})


def tenant_migration_files(migrations_dir: Path | None = None) -> list[Path]:
    """Ordered migration files that build a tenant schema (control-plane files excluded)."""
    directory = Path(migrations_dir or _MIGRATIONS_DIR)
    files = sorted(p for p in directory.glob("*.sql") if p.name not in _CONTROL_PLANE_MIGRATIONS)
    if not files:
        raise FileNotFoundError(
            f"No tenant migrations found in {directory}. Set TENANT_MIGRATIONS_DIR to the data/migrations directory."
        )
    return files


def apply_tenant_migrations(conn, schema_name: str, migrations_dir: Path | None = None) -> list[str]:
    """Apply every tenant migration inside *schema_name*, in order.

    All migrations are idempotent (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS),
    so this is safe both for a brand-new schema and for bringing an existing
    tenant up to date.  search_path is set to the tenant schema *only* (no
    public fallback) so an unqualified ALTER can never modify a public table.
    Runs in the caller's transaction; the caller commits or rolls back.

    Returns:
        Names of the migration files applied, in order.
    """
    safe = _safe_schema_name(schema_name)
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL search_path TO {safe}")
        for path in tenant_migration_files(migrations_dir):
            cur.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    logger.info("Applied %d migrations to schema %s", len(applied), safe)
    return applied


# ─── Data classes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    slug: str
    name: str
    schema_name: str
    plan: str
    active: bool
    role: str = "hr_admin"
    stripe_customer_id: str | None = None


# ─── Schema routing ───────────────────────────────────────────────────────────


def set_search_path(conn, schema_name: str) -> None:
    """Point the connection at the tenant schema for the remainder of this session."""
    safe = _safe_schema_name(schema_name)
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {safe}, public")


def _safe_schema_name(name: str) -> str:
    """Validate and return the schema name; raise if it looks suspicious."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise ValueError(f"Invalid schema name: {name!r}")
    return name


# ─── API key helpers ──────────────────────────────────────────────────────────


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash).  raw_key is shown once; store only key_hash."""
    raw = secrets.token_urlsafe(32)
    return raw, _hash_key(raw)


# ─── Tenant resolution (auth) ─────────────────────────────────────────────────


def resolve_tenant(tenant_id: str, api_key: str, conn) -> TenantContext | None:
    """Look up a tenant by ID and validate the API key.

    Returns TenantContext if credentials are valid and tenant is active, else None.
    Updates last_used on the matching API key row.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id::text, t.slug, t.name, t.schema_name, t.plan,
                   t.active, t.stripe_customer_id,
                   k.id AS key_id, k.key_hash,
                   COALESCE(k.role, 'hr_admin') AS role
            FROM public.tenants t
            JOIN public.tenant_api_keys k ON k.tenant_id = t.id
            WHERE t.id = %s::uuid
              AND t.active = true
              AND k.active = true
            """,
            (tenant_id,),
        )
        rows = cur.fetchall()

    key_hash = _hash_key(api_key)
    for row in rows:
        if row["key_hash"] == key_hash:
            # Update last_used asynchronously (best-effort; don't block auth)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE public.tenant_api_keys SET last_used = NOW() WHERE id = %s",
                        (row["key_id"],),
                    )
            except Exception:
                pass
            return TenantContext(
                tenant_id=row["id"],
                slug=row["slug"],
                name=row["name"],
                schema_name=row["schema_name"],
                plan=row["plan"],
                active=row["active"],
                role=row["role"],
                stripe_customer_id=row["stripe_customer_id"],
            )
    return None


# ─── Tenant provisioning ──────────────────────────────────────────────────────


def provision_tenant_schema(
    slug: str,
    name: str,
    plan: str,
    conn,
) -> dict:
    """Create a new tenant: register in public.tenants, create schema, run DDL.

    Args:
        slug: URL-safe identifier used in Kafka topics and schema name.
        name: Human-readable company name.
        plan: One of free / starter / pro / enterprise.
        conn: Admin DB connection (search_path NOT set to a tenant schema).

    Returns:
        dict with tenant_id, slug, schema_name, raw_api_key.
        raw_api_key is shown exactly once — it is NOT stored; only its hash is.

    Raises:
        ValueError: if slug is already taken or invalid.
        psycopg2.Error: on any DB failure (caller should rollback).
    """
    slug = slug.lower().strip()
    schema_name = f"tenant_{re.sub(r'[^a-z0-9]', '_', slug)}"

    raw_key, key_hash = generate_api_key()

    # 1. Insert tenant row
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.tenants (slug, name, plan, schema_name)
            VALUES (%s, %s, %s, %s)
            RETURNING id::text
            """,
            (slug, name, plan, schema_name),
        )
        tenant_id = cur.fetchone()["id"]

    # 2. Create API key
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.tenant_api_keys (tenant_id, key_hash, name)
            VALUES (%s::uuid, %s, 'default')
            """,
            (tenant_id, key_hash),
        )

    # 3. Create schema
    safe_schema = _safe_schema_name(schema_name)
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {safe_schema}")

    # 4. Build the tenant schema from the migration set (same DDL as the default schema)
    apply_tenant_migrations(conn, safe_schema)

    conn.commit()
    logger.info("Tenant provisioned: slug=%s schema=%s id=%s", slug, schema_name, tenant_id)

    return {
        "tenant_id": tenant_id,
        "slug": slug,
        "name": name,
        "plan": plan,
        "schema_name": schema_name,
        "raw_api_key": raw_key,
    }


def deprovision_tenant(tenant_id: str, conn, *, drop_schema: bool = False) -> None:
    """Soft-delete a tenant (active = false).

    Optionally drops the tenant schema entirely — this is irreversible and
    requires explicit opt-in via drop_schema=True.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.tenants SET active = false, updated_at = NOW() WHERE id = %s::uuid",
            (tenant_id,),
        )
        if drop_schema:
            cur.execute(
                "SELECT schema_name FROM public.tenants WHERE id = %s::uuid",
                (tenant_id,),
            )
            row = cur.fetchone()
            if row:
                safe = _safe_schema_name(row["schema_name"])
                cur.execute(f"DROP SCHEMA IF EXISTS {safe} CASCADE")
    conn.commit()


# ─── Usage tracking ───────────────────────────────────────────────────────────


def record_usage(tenant_id: str, event_count: int, conn) -> None:
    """Increment the event counter for the current billing month.

    Uses ON CONFLICT DO UPDATE for idempotent increments from Kafka consumers.
    Operates on the public schema (not the tenant schema).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.tenant_usage (tenant_id, month, event_count)
            VALUES (%s::uuid, DATE_TRUNC('month', NOW())::date, %s)
            ON CONFLICT (tenant_id, month)
            DO UPDATE SET
                event_count = tenant_usage.event_count + EXCLUDED.event_count,
                updated_at  = NOW()
            """,
            (tenant_id, event_count),
        )


def fetch_tenant_usage(tenant_id: str, months: int, conn) -> list[dict]:
    """Return per-month event counts for the last N months, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT month, event_count, reported_to_stripe
            FROM public.tenant_usage
            WHERE tenant_id = %s::uuid
            ORDER BY month DESC
            LIMIT %s
            """,
            (tenant_id, months),
        )
        return [dict(r) for r in cur.fetchall()]


def fetch_current_usage(tenant_id: str, conn) -> int:
    """Return event count for the current billing month."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(event_count, 0)
            FROM public.tenant_usage
            WHERE tenant_id = %s::uuid
              AND month = DATE_TRUNC('month', NOW())::date
            """,
            (tenant_id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0
