-- Migration 019: statistics for churn, peer exposure, equity and anomaly models
-- (MATH_MODEL.md §8, §9.1, §9.2, §10.5)
-- Run after 018_sudden_withdrawal_alert.sql.
--
-- A churn label (left within 90 days of t) is only known 90 days after t, and
-- raw_events are purged after 90 days, so the features an employee had at t
-- can no longer be recomputed when the label arrives.  The daily scorer
-- therefore logs the exact feature vector it scored, and training reads these
-- rows: train and serve use identical features, with no look-ahead.
--
-- Also adds the departure date the trainer labels from, and the peer-exposure
-- p-value written by the contagion test.
-- Idempotent.

CREATE TABLE IF NOT EXISTS churn_feature_log (
    employee_id      UUID        NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    feature_date     DATE        NOT NULL,
    feature_version  TEXT        NOT NULL,
    features         DOUBLE PRECISION[] NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (employee_id, feature_date, feature_version)
);

CREATE INDEX IF NOT EXISTS idx_churn_feature_log_date ON churn_feature_log (feature_date);

ALTER TABLE churn_scores
    ADD COLUMN IF NOT EXISTS peer_exposure_p  FLOAT,
    ADD COLUMN IF NOT EXISTS risk_drivers     JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ─── §10.5: equity flags need a significance test, not just a ratio ──────────
ALTER TABLE structural_equity_scores
    ADD COLUMN IF NOT EXISTS mean_ratio    FLOAT,   -- group mean / mean of everyone else (test statistic)
    ADD COLUMN IF NOT EXISTS effect_size   FLOAT,   -- P(member ranks above non-member), 0.5 = parity
    ADD COLUMN IF NOT EXISTS p_value       FLOAT,   -- one-sided permutation p (group lower)
    ADD COLUMN IF NOT EXISTS q_value       FLOAT;   -- Benjamini–Hochberg adjusted across the run
