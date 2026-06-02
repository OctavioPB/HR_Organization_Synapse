-- Migration 012: Departure impact reports and onboarding tracker

-- Add deactivated_at to employees
ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

-- Add hire_date to employees (also used by onboarding tracker)
ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS hire_date DATE;

-- Departure impact reports
CREATE TABLE IF NOT EXISTS departure_impact_reports (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id    UUID        NOT NULL REFERENCES employees(id),
  departure_date DATE        NOT NULL,
  generated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  impact_json    JSONB       NOT NULL DEFAULT '{}',
  narrative_text TEXT,
  status         TEXT        NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'ready', 'failed')),
  UNIQUE (employee_id, departure_date)
);

CREATE INDEX IF NOT EXISTS idx_departure_reports_employee    ON departure_impact_reports (employee_id);
CREATE INDEX IF NOT EXISTS idx_departure_reports_status      ON departure_impact_reports (status);
CREATE INDEX IF NOT EXISTS idx_departure_reports_generated   ON departure_impact_reports (generated_at DESC);

-- Onboarding integration scores (also created here for atomic migration)
CREATE TABLE IF NOT EXISTS onboarding_integration_scores (
  id                     UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id            UUID    NOT NULL REFERENCES employees(id),
  scored_date            DATE    NOT NULL,
  integration_score      FLOAT   NOT NULL DEFAULT 0.0,
  degree_centrality_pct  FLOAT,
  cross_dept_edge_count  INT     DEFAULT 0,
  community_stability    FLOAT   DEFAULT 1.0,
  cohort_size            INT     DEFAULT 0,
  below_cohort_threshold BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (employee_id, scored_date)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_employee ON onboarding_integration_scores (employee_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_date     ON onboarding_integration_scores (scored_date DESC);
