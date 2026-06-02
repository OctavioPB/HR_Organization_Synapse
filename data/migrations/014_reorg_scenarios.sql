-- Migration 014: Reorg scenario planner
CREATE TABLE IF NOT EXISTS reorg_scenarios (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  description   TEXT,
  operations    JSONB       NOT NULL DEFAULT '[]',
  impact_report JSONB,
  status        TEXT        NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'computed', 'archived')),
  created_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  computed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_reorg_status     ON reorg_scenarios (status);
CREATE INDEX IF NOT EXISTS idx_reorg_created_at ON reorg_scenarios (created_at DESC);
