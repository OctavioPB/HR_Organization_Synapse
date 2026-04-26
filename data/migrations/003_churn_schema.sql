-- Migration 003: churn risk schema + HR employee columns + teams channel
-- Run after 001_app_schema.sql and 002_add_connectivity_anomaly_alert_type.sql

-- ─── Extend employees with HR features needed by the GNN ─────────────────────
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS hire_date      DATE,
    ADD COLUMN IF NOT EXISTS role_level     SMALLINT CHECK (role_level BETWEEN 1 AND 7),
    ADD COLUMN IF NOT EXISTS pto_days_used  SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS left_at        TIMESTAMPTZ;

-- ─── Fix raw_events channel constraint to include 'teams' ─────────────────────
ALTER TABLE raw_events
    DROP CONSTRAINT IF EXISTS raw_events_channel_check;

ALTER TABLE raw_events
    ADD CONSTRAINT raw_events_channel_check
        CHECK (channel IN ('slack','email','jira','calendar','github','teams'));

-- ─── Churn labels (historical ground truth) ───────────────────────────────────
-- Populated manually or by an HR integration when an employee formally leaves.
-- Used exclusively as training labels for the GNN — not shown to end users.
CREATE TABLE IF NOT EXISTS churn_labels (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     UUID        NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    label_date      DATE        NOT NULL,
    churned         BOOLEAN     NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, label_date)
);

CREATE INDEX IF NOT EXISTS idx_churn_labels_employee  ON churn_labels (employee_id);
CREATE INDEX IF NOT EXISTS idx_churn_labels_date      ON churn_labels (label_date);
CREATE INDEX IF NOT EXISTS idx_churn_labels_churned   ON churn_labels (churned) WHERE churned = true;

-- ─── Churn scores (daily GNN predictions) ─────────────────────────────────────
-- Written by ml/gnn/scorer.py after each daily scoring run.
-- churn_prob: model output after sigmoid, range [0, 1].
-- risk_tier: bucketed label for dashboard display.
CREATE TABLE IF NOT EXISTS churn_scores (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     UUID        NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    scored_at       DATE        NOT NULL DEFAULT CURRENT_DATE,
    churn_prob      FLOAT       NOT NULL CHECK (churn_prob >= 0.0 AND churn_prob <= 1.0),
    risk_tier       TEXT        NOT NULL CHECK (risk_tier IN ('high','medium','low')),
    model_version   TEXT        NOT NULL DEFAULT 'unknown',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, scored_at)
);

CREATE INDEX IF NOT EXISTS idx_churn_scores_employee  ON churn_scores (employee_id);
CREATE INDEX IF NOT EXISTS idx_churn_scores_scored_at ON churn_scores (scored_at);
CREATE INDEX IF NOT EXISTS idx_churn_scores_risk_tier ON churn_scores (risk_tier);
