-- Migration 013: HRIS enrichment columns on employees table
ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS tenure_months        SMALLINT,
  ADD COLUMN IF NOT EXISTS days_since_promotion SMALLINT,
  ADD COLUMN IF NOT EXISTS is_comp_band_max     BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS pto_days_ytd         SMALLINT,
  ADD COLUMN IF NOT EXISTS reporting_level      SMALLINT,  -- 1=IC1 … 7=C-Suite
  ADD COLUMN IF NOT EXISTS hris_source          TEXT,      -- 'workday' | 'bamboohr'
  ADD COLUMN IF NOT EXISTS hris_synced_at       TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_employees_hris_source ON employees (hris_source);
