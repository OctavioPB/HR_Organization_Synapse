-- F8: Compliance & Regulatory Reporting — audit log and retention tracking tables.

-- ─── Consent audit log ────────────────────────────────────────────────────────
-- Every consent change (grant or revoke) is recorded here for GDPR accountability.

CREATE TABLE IF NOT EXISTS consent_audit_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     UUID        NOT NULL REFERENCES employees(id),
    changed_by      TEXT        NOT NULL,   -- 'employee', 'hr_admin', 'system'
    previous_value  BOOLEAN     NOT NULL,
    new_value       BOOLEAN     NOT NULL,
    reason          TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consent_audit_employee ON consent_audit_log (employee_id);
CREATE INDEX IF NOT EXISTS idx_consent_audit_changed_at ON consent_audit_log (changed_at DESC);

-- ─── Data retention purge log ─────────────────────────────────────────────────
-- Each automated or manual purge run is recorded for audit trail.

CREATE TABLE IF NOT EXISTS data_retention_purges (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    purged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    table_name      TEXT        NOT NULL,
    rows_deleted    BIGINT      NOT NULL DEFAULT 0,
    cutoff_date     DATE        NOT NULL,
    triggered_by    TEXT        NOT NULL DEFAULT 'airflow',  -- 'airflow', 'api', 'manual'
    status          TEXT        NOT NULL DEFAULT 'completed'
                                CHECK (status IN ('completed', 'failed', 'partial'))
);

CREATE INDEX IF NOT EXISTS idx_purges_purged_at ON data_retention_purges (purged_at DESC);
CREATE INDEX IF NOT EXISTS idx_purges_table     ON data_retention_purges (table_name);
