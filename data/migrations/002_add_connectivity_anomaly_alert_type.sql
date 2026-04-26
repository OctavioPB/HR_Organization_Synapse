-- Migration 002: expand alerts.type CHECK to include connectivity_anomaly
-- Required by ml/anomaly/isolation_forest.py (Sprint 4).

ALTER TABLE alerts
    DROP CONSTRAINT IF EXISTS alerts_type_check;

ALTER TABLE alerts
    ADD CONSTRAINT alerts_type_check
    CHECK (type IN (
        'silo',
        'spof_critical',
        'withdrawing',
        'pipeline_failure',
        'connectivity_anomaly'
    ));
