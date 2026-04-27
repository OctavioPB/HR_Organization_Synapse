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
    record_usage(tenant_id, event_count, conn) -> None
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ─── Plan limits ──────────────────────────────────────────────────────────────

PLAN_LIMITS: dict[str, dict] = {
    "free":       {"events_per_month": 10_000,     "employees_max": 50},
    "starter":    {"events_per_month": 100_000,    "employees_max": 200},
    "pro":        {"events_per_month": 1_000_000,  "employees_max": 1_000},
    "enterprise": {"events_per_month": None,        "employees_max": None},
}

# ─── Per-tenant table DDL ─────────────────────────────────────────────────────
# Embedded so provisioning works without reading files from disk.
# search_path is set to the tenant schema before executing this SQL.

_TENANT_TABLES_SQL = """
-- Core tables (001_app_schema)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS employees (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    department  TEXT        NOT NULL,
    role        TEXT        NOT NULL,
    active      BOOLEAN     NOT NULL DEFAULT true,
    consent     BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   UUID        NOT NULL REFERENCES employees(id),
    target_id   UUID        NOT NULL REFERENCES employees(id),
    channel     TEXT        NOT NULL
                CHECK (channel IN ('slack','email','jira','calendar','github')),
    direction   TEXT        NOT NULL
                CHECK (direction IN ('sent','mentioned','invited','assigned','reviewed')),
    ts          TIMESTAMPTZ NOT NULL,
    weight      FLOAT       NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_raw_events_ts        ON raw_events (ts);
CREATE INDEX IF NOT EXISTS idx_raw_events_source_id ON raw_events (source_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_target_id ON raw_events (target_id);

CREATE TABLE IF NOT EXISTS graph_snapshots (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date   DATE    NOT NULL,
    employee_id     UUID    NOT NULL REFERENCES employees(id),
    betweenness     FLOAT,
    degree_in       FLOAT,
    degree_out      FLOAT,
    clustering      FLOAT,
    community_id    INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON graph_snapshots (snapshot_date);

CREATE TABLE IF NOT EXISTS risk_scores (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    employee_id     UUID        NOT NULL REFERENCES employees(id),
    spof_score      FLOAT       NOT NULL,
    entropy_trend   FLOAT,
    anomaly_score   FLOAT,
    flag            TEXT
    CHECK (flag IN ('critical','warning','withdrawing','normal'))
);
CREATE INDEX IF NOT EXISTS idx_risk_scores_employee  ON risk_scores (employee_id);
CREATE INDEX IF NOT EXISTS idx_risk_scores_scored_at ON risk_scores (scored_at);

CREATE TABLE IF NOT EXISTS alerts (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    fired_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    type              TEXT        NOT NULL
    CHECK (type IN ('silo','spof_critical','withdrawing','pipeline_failure','connectivity_anomaly')),
    severity          TEXT        NOT NULL
    CHECK (severity IN ('critical','high','medium','low')),
    affected_entities JSONB       NOT NULL DEFAULT '[]',
    details           TEXT,
    resolved          BOOLEAN     NOT NULL DEFAULT false,
    resolved_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alerts_fired_at ON alerts (fired_at);
CREATE INDEX IF NOT EXISTS idx_alerts_type     ON alerts (type);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts (resolved);

-- F1: GNN churn risk
CREATE TABLE IF NOT EXISTS churn_risk_scores (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    scored_at       DATE    NOT NULL,
    employee_id     UUID    NOT NULL REFERENCES employees(id),
    churn_prob      FLOAT   NOT NULL CHECK (churn_prob BETWEEN 0 AND 1),
    risk_tier       TEXT    NOT NULL CHECK (risk_tier IN ('high','medium','low')),
    model_version   TEXT    NOT NULL,
    feature_snapshot JSONB  NOT NULL DEFAULT '{}',
    UNIQUE (employee_id, scored_at)
);
CREATE INDEX IF NOT EXISTS idx_churn_scored_at ON churn_risk_scores (scored_at DESC);

-- F2: Temporal anomaly
CREATE TABLE IF NOT EXISTS temporal_anomaly_scores (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    scored_at           DATE    NOT NULL,
    employee_id         UUID    NOT NULL REFERENCES employees(id),
    anomaly_score       FLOAT   NOT NULL CHECK (anomaly_score BETWEEN 0 AND 1),
    anomaly_tier        TEXT    NOT NULL CHECK (anomaly_tier IN ('high','medium','low')),
    reconstruction_error FLOAT  NOT NULL,
    trend_slope         FLOAT   NOT NULL DEFAULT 0.0,
    model_version       TEXT    NOT NULL DEFAULT 'temporal_gae_v1',
    UNIQUE (employee_id, scored_at)
);
CREATE INDEX IF NOT EXISTS idx_temporal_scored_at ON temporal_anomaly_scores (scored_at DESC);

-- F3: Knowledge risk
CREATE TABLE IF NOT EXISTS employee_knowledge (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     UUID    NOT NULL REFERENCES employees(id),
    domain          TEXT    NOT NULL,
    doc_count       INT     NOT NULL DEFAULT 0,
    is_sole_expert  BOOLEAN NOT NULL DEFAULT false,
    expertise_score FLOAT   NOT NULL DEFAULT 0.0,
    computed_at     DATE    NOT NULL,
    UNIQUE (employee_id, domain, computed_at)
);

CREATE TABLE IF NOT EXISTS knowledge_risk_scores (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id         UUID    NOT NULL REFERENCES employees(id),
    computed_at         DATE    NOT NULL,
    knowledge_score     FLOAT   NOT NULL,
    sole_expert_count   INT     NOT NULL DEFAULT 0,
    domain_count        INT     NOT NULL DEFAULT 0,
    doc_count           INT     NOT NULL DEFAULT 0,
    enhanced_spof_score FLOAT,
    impacted_departments JSONB  NOT NULL DEFAULT '[]',
    UNIQUE (employee_id, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_risk_computed_at
    ON knowledge_risk_scores (computed_at DESC);

-- F4: Succession planning
CREATE TABLE IF NOT EXISTS succession_recommendations (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at             DATE        NOT NULL,
    source_employee_id      UUID        NOT NULL REFERENCES employees(id),
    candidate_employee_id   UUID        NOT NULL REFERENCES employees(id),
    compatibility_score     FLOAT       NOT NULL CHECK (compatibility_score BETWEEN 0 AND 1),
    rank                    SMALLINT    NOT NULL CHECK (rank >= 1),
    structural_overlap      FLOAT,
    clustering_score        FLOAT,
    domain_overlap          FLOAT,
    rationale               JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_employee_id, candidate_employee_id, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_succession_computed_at
    ON succession_recommendations (computed_at DESC);

-- F9: Org health score
CREATE TABLE IF NOT EXISTS org_health_scores (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at       DATE        NOT NULL,
    score             FLOAT       NOT NULL CHECK (score BETWEEN 0 AND 100),
    tier              TEXT        NOT NULL
    CHECK (tier IN ('healthy','caution','at_risk','critical')),
    silo_count        INT         NOT NULL DEFAULT 0,
    avg_spof_score    FLOAT       NOT NULL DEFAULT 0.0,
    avg_entropy_trend FLOAT,
    wcc_count         INT         NOT NULL DEFAULT 1,
    node_count        INT         NOT NULL DEFAULT 0,
    component_scores  JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (computed_at)
);
CREATE INDEX IF NOT EXISTS idx_org_health_computed_at
    ON org_health_scores (computed_at DESC);
"""


# ─── Data classes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    slug: str
    name: str
    schema_name: str
    plan: str
    active: bool
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
                   k.id AS key_id, k.key_hash
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

    # 4. Run DDL inside the tenant schema
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL search_path TO {safe_schema}, public")
        cur.execute(_TENANT_TABLES_SQL)

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
