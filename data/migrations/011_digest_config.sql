-- Migration 011: Weekly digest configuration per tenant schema
CREATE TABLE IF NOT EXISTS digest_config (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  email_recipients  JSONB       NOT NULL DEFAULT '[]',
  slack_webhook_url TEXT,
  enabled_email     BOOLEAN     NOT NULL DEFAULT FALSE,
  enabled_slack     BOOLEAN     NOT NULL DEFAULT FALSE,
  timezone          TEXT        NOT NULL DEFAULT 'UTC',
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extend alerts table to accept new types used by digest and departure features
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
      'departure_report_ready'
    )
  );
