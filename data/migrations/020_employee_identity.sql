-- Migration 020: stable unique identifiers for employees + departure type
-- Run after 019_model_statistics.sql.
--
-- employees.id (UUID) is the internal key, but nothing tied an employee to the
-- outside world, so the HRIS sync matched on `LOWER(name) = email` or on *any*
-- employee whose role contained the e-mail prefix (LIMIT 1) — it could update
-- the wrong person.  Two external identifiers are added, each unique:
--
--   work_email                    unique case-insensitively
--   (hris_source, hris_external_id)  the HRIS's own worker / employee ID
--
-- The sync matches on these only (ingestion/connectors/hris_connector.py) and
-- reports unmatched records instead of guessing.  Link existing employees with
-- scripts/import_employee_identities.py.
--
-- departure_type distinguishes voluntary from involuntary exits: the churn
-- model predicts voluntary departure, so involuntary exits are not positives.
-- Idempotent.

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS work_email        TEXT,
    ADD COLUMN IF NOT EXISTS hris_external_id  TEXT,
    ADD COLUMN IF NOT EXISTS departure_type    TEXT;

ALTER TABLE employees DROP CONSTRAINT IF EXISTS employees_departure_type_check;
ALTER TABLE employees
    ADD CONSTRAINT employees_departure_type_check
        CHECK (departure_type IS NULL OR departure_type IN ('voluntary', 'involuntary', 'unknown'));

-- An e-mail address must not be shared by two employees, whatever its case.
CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_work_email
    ON employees (LOWER(work_email)) WHERE work_email IS NOT NULL;

-- One HRIS record maps to at most one employee.
CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_hris_identity
    ON employees (hris_source, hris_external_id) WHERE hris_external_id IS NOT NULL;
