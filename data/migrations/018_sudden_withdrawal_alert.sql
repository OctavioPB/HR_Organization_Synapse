-- Migration 018: sudden-withdrawal alert type (MATH_MODEL.md §4.2)
-- Run after 017_model_v2_v3_corrections.sql.
--
-- ml/features/changepoint.py detects an abrupt drop in an employee's daily
-- activity rate (Poisson likelihood-ratio change-point).  The smoothed, shrunk
-- entropy trend reacts slowly to an overnight stop; this alert covers that case.
-- Idempotent: drops and re-adds the CHECK constraint with the full type list.

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
            'peer_contagion_risk',
            'sudden_withdrawal'
        )
    );
