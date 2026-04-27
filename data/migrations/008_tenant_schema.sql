-- F6: Multi-Tenant SaaS — public (shared) schema tables.
--
-- Tenant data lives in per-tenant schemas (tenant_{slug}).
-- This migration only creates the shared registry and billing tables.
-- Per-tenant table provisioning is handled by api/tenant.py:provision_tenant_schema().

-- ─── Tenant registry ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.tenants (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                TEXT        NOT NULL UNIQUE
                                    CHECK (slug ~ '^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$'),
    name                TEXT        NOT NULL,
    plan                TEXT        NOT NULL DEFAULT 'free'
                                    CHECK (plan IN ('free','starter','pro','enterprise')),
    schema_name         TEXT        NOT NULL UNIQUE,
    stripe_customer_id  TEXT,
    stripe_subscription_id TEXT,
    active              BOOLEAN     NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_slug   ON public.tenants (slug);
CREATE INDEX IF NOT EXISTS idx_tenants_active ON public.tenants (active);

-- ─── Tenant API keys ──────────────────────────────────────────────────────────
-- key_hash is SHA-256(raw_key) stored as hex.  Raw key shown once at creation.

CREATE TABLE IF NOT EXISTS public.tenant_api_keys (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    key_hash    TEXT        NOT NULL,
    name        TEXT        NOT NULL DEFAULT 'default',
    active      BOOLEAN     NOT NULL DEFAULT true,
    last_used   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_id ON public.tenant_api_keys (tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash      ON public.tenant_api_keys (key_hash);

-- ─── Usage tracking (for billing) ────────────────────────────────────────────
-- One row per (tenant, billing month).  Updated incrementally by Kafka consumers.

CREATE TABLE IF NOT EXISTS public.tenant_usage (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    month               DATE        NOT NULL,   -- always first day of month
    event_count         BIGINT      NOT NULL DEFAULT 0,
    reported_to_stripe  BOOLEAN     NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, month)
);

CREATE INDEX IF NOT EXISTS idx_tenant_usage_tenant_month
    ON public.tenant_usage (tenant_id, month DESC);

-- ─── Stripe webhook log (for idempotency) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.stripe_webhook_events (
    stripe_event_id TEXT        PRIMARY KEY,
    type            TEXT        NOT NULL,
    payload         JSONB       NOT NULL DEFAULT '{}',
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.tenants IS
    'Org Synapse tenant registry.  One row per customer organisation.';
COMMENT ON TABLE public.tenant_api_keys IS
    'Hashed API keys for tenant authentication.  Raw key shown once at creation only.';
COMMENT ON TABLE public.tenant_usage IS
    'Monthly event ingestion counts per tenant, used for Stripe usage-based billing.';
