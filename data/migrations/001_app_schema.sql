-- Org Synapse application schema
-- Target database: org_synapse (set by POSTGRES_DB in docker-compose)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Employees ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS employees (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    department    TEXT        NOT NULL,
    role          TEXT        NOT NULL,
    active        BOOLEAN     NOT NULL DEFAULT true,
    consent       BOOLEAN     NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Raw collaboration events ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_events (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id     UUID        NOT NULL REFERENCES employees(id),
    target_id     UUID        NOT NULL REFERENCES employees(id),
    channel       TEXT        NOT NULL CHECK (channel IN ('slack','email','jira','calendar','github')),
    direction     TEXT        NOT NULL CHECK (direction IN ('sent','mentioned','invited','assigned','reviewed')),
    ts            TIMESTAMPTZ NOT NULL,
    weight        FLOAT       NOT NULL DEFAULT 1.0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_events_ts        ON raw_events (ts);
CREATE INDEX IF NOT EXISTS idx_raw_events_source_id ON raw_events (source_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_target_id ON raw_events (target_id);

-- ─── Daily graph snapshots ────────────────────────────────────────────────────
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

-- ─── Risk scores ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risk_scores (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    employee_id     UUID        NOT NULL REFERENCES employees(id),
    spof_score      FLOAT       NOT NULL,
    entropy_trend   FLOAT,
    anomaly_score   FLOAT,
    flag            TEXT        CHECK (flag IN ('critical','warning','withdrawing','normal'))
);

CREATE INDEX IF NOT EXISTS idx_risk_scores_employee ON risk_scores (employee_id);
CREATE INDEX IF NOT EXISTS idx_risk_scores_scored_at ON risk_scores (scored_at);

-- ─── Alerts ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    fired_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    type              TEXT        NOT NULL CHECK (type IN ('silo','spof_critical','withdrawing','pipeline_failure')),
    severity          TEXT        NOT NULL CHECK (severity IN ('critical','high','medium','low')),
    affected_entities JSONB       NOT NULL DEFAULT '[]',
    details           TEXT,
    resolved          BOOLEAN     NOT NULL DEFAULT false,
    resolved_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alerts_fired_at ON alerts (fired_at);
CREATE INDEX IF NOT EXISTS idx_alerts_type     ON alerts (type);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts (resolved);
