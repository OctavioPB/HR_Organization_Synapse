-- Migration 005: knowledge risk schema
-- Run after 004_temporal_schema.sql

-- ─── Knowledge domain catalog ─────────────────────────────────────────────────
-- One row per logical knowledge area (e.g. "payment-systems", "devops").
-- Populated automatically by connectors from Confluence space names /
-- Notion database titles, and optionally curated manually.
CREATE TABLE IF NOT EXISTS knowledge_domains (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    source      TEXT    NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('confluence','notion','manual')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Raw document knowledge metadata ─────────────────────────────────────────
-- Written by Confluence and Notion batch connectors.
-- Only metadata is stored — never document body or content.
-- UNIQUE (source, doc_id) makes re-ingestion idempotent.
CREATE TABLE IF NOT EXISTS document_knowledge (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT        NOT NULL CHECK (source IN ('confluence','notion')),
    doc_id          TEXT        NOT NULL,
    title           TEXT,
    author_id       UUID        REFERENCES employees(id) ON DELETE SET NULL,
    contributor_ids UUID[]      NOT NULL DEFAULT '{}',
    domain_tags     TEXT[]      NOT NULL DEFAULT '{}',
    last_modified_at TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_doc_knowledge_author    ON document_knowledge (author_id);
CREATE INDEX IF NOT EXISTS idx_doc_knowledge_source    ON document_knowledge (source);
CREATE INDEX IF NOT EXISTS idx_doc_knowledge_modified  ON document_knowledge (last_modified_at);
-- GIN index for fast array membership searches
CREATE INDEX IF NOT EXISTS idx_doc_knowledge_domains   ON document_knowledge USING GIN (domain_tags);
CREATE INDEX IF NOT EXISTS idx_doc_knowledge_contribs  ON document_knowledge USING GIN (contributor_ids);

-- ─── Per-employee domain expertise ────────────────────────────────────────────
-- Computed daily by graph/knowledge_risk.py; UPSERT on (employee_id, domain, computed_at).
-- expertise_score: normalised [0,1] based on doc_count and sole expert status.
CREATE TABLE IF NOT EXISTS employee_knowledge (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     UUID        NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    domain          TEXT        NOT NULL,
    doc_count       INTEGER     NOT NULL DEFAULT 0,
    is_sole_expert  BOOLEAN     NOT NULL DEFAULT false,
    expertise_score FLOAT       NOT NULL DEFAULT 0.0,
    computed_at     DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, domain, computed_at)
);

CREATE INDEX IF NOT EXISTS idx_emp_knowledge_employee   ON employee_knowledge (employee_id);
CREATE INDEX IF NOT EXISTS idx_emp_knowledge_domain     ON employee_knowledge (domain);
CREATE INDEX IF NOT EXISTS idx_emp_knowledge_computed   ON employee_knowledge (computed_at);
CREATE INDEX IF NOT EXISTS idx_emp_knowledge_sole       ON employee_knowledge (is_sole_expert)
    WHERE is_sole_expert = true;

-- ─── Knowledge risk scores ────────────────────────────────────────────────────
-- Rolled-up risk score per employee; enhanced_spof_score combines graph SPOF
-- with knowledge concentration.  impacted_departments is a JSON array of
-- department names that lose all experts in at least one domain if this
-- employee leaves.
CREATE TABLE IF NOT EXISTS knowledge_risk_scores (
    id                      UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id             UUID    NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    knowledge_score         FLOAT   NOT NULL DEFAULT 0.0
                                    CHECK (knowledge_score >= 0.0 AND knowledge_score <= 1.0),
    sole_expert_count       INTEGER NOT NULL DEFAULT 0,
    domain_count            INTEGER NOT NULL DEFAULT 0,
    doc_count               INTEGER NOT NULL DEFAULT 0,
    enhanced_spof_score     FLOAT,
    impacted_departments    JSONB   NOT NULL DEFAULT '[]',
    computed_at             DATE    NOT NULL DEFAULT CURRENT_DATE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, computed_at)
);

CREATE INDEX IF NOT EXISTS idx_know_risk_employee  ON knowledge_risk_scores (employee_id);
CREATE INDEX IF NOT EXISTS idx_know_risk_computed  ON knowledge_risk_scores (computed_at);
CREATE INDEX IF NOT EXISTS idx_know_risk_score     ON knowledge_risk_scores (knowledge_score DESC);
CREATE INDEX IF NOT EXISTS idx_know_risk_enhanced  ON knowledge_risk_scores (enhanced_spof_score DESC NULLS LAST);
