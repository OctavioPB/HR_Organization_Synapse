-- Migration 006: Succession Planning Recommendations
-- Stores cross-training candidate pairs for high-SPOF employees.

CREATE TABLE IF NOT EXISTS succession_recommendations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at           DATE NOT NULL,
    source_employee_id    UUID NOT NULL REFERENCES employees(id),
    candidate_employee_id UUID NOT NULL REFERENCES employees(id),
    compatibility_score   FLOAT NOT NULL CHECK (compatibility_score BETWEEN 0 AND 1),
    rank                  SMALLINT NOT NULL CHECK (rank >= 1),
    structural_overlap    FLOAT CHECK (structural_overlap BETWEEN 0 AND 1),
    clustering_score      FLOAT CHECK (clustering_score BETWEEN 0 AND 1),
    domain_overlap        FLOAT CHECK (domain_overlap BETWEEN 0 AND 1),
    rationale             JSONB,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_employee_id, candidate_employee_id, computed_at)
);

CREATE INDEX IF NOT EXISTS idx_succession_computed_at
    ON succession_recommendations(computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_succession_source
    ON succession_recommendations(source_employee_id, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_succession_candidate
    ON succession_recommendations(candidate_employee_id);

COMMENT ON TABLE succession_recommendations IS
    'Cross-training candidate pairs: for each high-SPOF source employee, '
    'lists employees in neighboring communities ranked by compatibility score.';
