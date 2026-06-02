-- Migration 016: DEI structural equity analytics
CREATE TABLE IF NOT EXISTS employee_demographics (
  employee_id  UUID    PRIMARY KEY REFERENCES employees(id),
  gender_group TEXT,   -- anonymised group label e.g. 'group_a', 'group_b'
  tenure_band  TEXT,   -- '0-1y', '1-3y', '3-5y', '5y+'
  level_band   TEXT,   -- 'ic', 'senior_ic', 'manager', 'director_plus'
  consent      BOOLEAN NOT NULL DEFAULT FALSE,
  source       TEXT    -- 'hris_import' | 'manual'
);

CREATE TABLE IF NOT EXISTS structural_equity_scores (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  computed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dimension        TEXT        NOT NULL,  -- 'gender_group' | 'tenure_band' | 'level_band'
  group_value      TEXT        NOT NULL,
  metric           TEXT        NOT NULL,  -- 'betweenness' | 'cross_dept_ratio' | 'degree'
  median_score     FLOAT,
  p25_score        FLOAT,
  p75_score        FLOAT,
  member_count     INT,
  below_org_median BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_equity_dimension   ON structural_equity_scores (dimension, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_equity_computed_at ON structural_equity_scores (computed_at DESC);
