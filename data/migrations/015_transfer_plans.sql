-- Migration 015: Knowledge transfer plans
CREATE TABLE IF NOT EXISTS knowledge_transfer_plans (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  spof_employee_id  UUID        NOT NULL REFERENCES employees(id),
  candidate_id      UUID        NOT NULL REFERENCES employees(id),
  generated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  plan_json         JSONB       NOT NULL DEFAULT '{}',
  status            TEXT        NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'completed', 'archived')),
  UNIQUE (spof_employee_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_transfer_plans_spof ON knowledge_transfer_plans (spof_employee_id);
