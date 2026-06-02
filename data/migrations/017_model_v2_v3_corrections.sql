-- Migration 017: MODEL.md v2/v3 mathematical corrections
-- Run after 016_dei_equity.sql.
--
-- Adds the schema needed by:
--   §5.3/§5.5 SPOF weight-sensitivity bands + new flag tiers
--   §7.4.1    turnover-contagion (peer_churn_rate) alert layer
--   §7.4.2    churn explainability (top-attention influence neighbors)

-- ─── §5: SPOF sensitivity bands + new flag tiers ──────────────────────────────
ALTER TABLE risk_scores
    ADD COLUMN IF NOT EXISTS spof_score_lo FLOAT,
    ADD COLUMN IF NOT EXISTS spof_score_hi FLOAT,
    ADD COLUMN IF NOT EXISTS weight_robust BOOLEAN;

-- Extend the flag enumeration with the v2 tiers:
--   critical_uncertain — crosses 0.7 only under central weights (weight-sensitive)
--   elevated           — 0.4–0.5 band (monitor entropy trend)
ALTER TABLE risk_scores
    DROP CONSTRAINT IF EXISTS risk_scores_flag_check;

ALTER TABLE risk_scores
    ADD CONSTRAINT risk_scores_flag_check CHECK (
        flag IN (
            'critical',
            'critical_uncertain',
            'warning',
            'elevated',
            'withdrawing',
            'normal'
        )
    );

-- ─── §7.4: turnover contagion + explainability on churn_scores ────────────────
ALTER TABLE churn_scores
    ADD COLUMN IF NOT EXISTS peer_churn_rate     FLOAT,
    ADD COLUMN IF NOT EXISTS peer_contagion_risk BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS influence_neighbors JSONB   NOT NULL DEFAULT '[]';

-- ─── §7.4.1: new alert type for the contagion layer ───────────────────────────
ALTER TABLE alerts
    DROP CONSTRAINT IF EXISTS alerts_type_check;

ALTER TABLE alerts
    ADD CONSTRAINT alerts_type_check CHECK (
        type IN (
            'silo',
            'spof_critical',
            'withdrawing',
            'pipeline_failure',
            'connectivity_anomaly',
            'onboarding_risk',
            'departure_report_ready',
            'peer_contagion_risk'
        )
    );
