-- Migration 004: temporal graph analysis schema
-- Run after 003_churn_schema.sql

-- ─── Temporal anomaly scores ──────────────────────────────────────────────────
-- Written by graph/temporal/scorer.py after each daily scoring run.
-- anomaly_score: reconstruction error per employee, normalised to [0, 1].
-- anomaly_tier:  high >= 0.6, medium >= 0.3, low < 0.3.
-- trend_slope:   linear slope of anomaly_score over the last 4 weeks
--                (positive = worsening, negative = recovering).
-- reconstruction_error: raw MSE value before normalisation; useful for
--                       cross-run comparison and model performance tracking.
CREATE TABLE IF NOT EXISTS temporal_anomaly_scores (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id         UUID        NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    scored_at           DATE        NOT NULL DEFAULT CURRENT_DATE,
    anomaly_score       FLOAT       NOT NULL CHECK (anomaly_score >= 0.0 AND anomaly_score <= 1.0),
    anomaly_tier        TEXT        NOT NULL CHECK (anomaly_tier IN ('high','medium','low')),
    reconstruction_error FLOAT      NOT NULL,
    trend_slope         FLOAT       NOT NULL DEFAULT 0.0,
    model_version       TEXT        NOT NULL DEFAULT 'unknown',
    n_weeks             SMALLINT    NOT NULL DEFAULT 8,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, scored_at)
);

CREATE INDEX IF NOT EXISTS idx_temporal_anomaly_employee  ON temporal_anomaly_scores (employee_id);
CREATE INDEX IF NOT EXISTS idx_temporal_anomaly_scored_at ON temporal_anomaly_scores (scored_at);
CREATE INDEX IF NOT EXISTS idx_temporal_anomaly_tier      ON temporal_anomaly_scores (anomaly_tier);
CREATE INDEX IF NOT EXISTS idx_temporal_anomaly_score     ON temporal_anomaly_scores (anomaly_score DESC);
