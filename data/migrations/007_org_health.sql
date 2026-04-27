-- F9: Org Health Score — stores the weekly composite score and its component breakdown.

CREATE TABLE IF NOT EXISTS org_health_scores (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at       DATE        NOT NULL,
    score             FLOAT       NOT NULL CHECK (score >= 0 AND score <= 100),
    tier              TEXT        NOT NULL CHECK (tier IN ('healthy','caution','at_risk','critical')),
    silo_count        INT         NOT NULL DEFAULT 0,
    avg_spof_score    FLOAT       NOT NULL DEFAULT 0.0,
    avg_entropy_trend FLOAT,
    wcc_count         INT         NOT NULL DEFAULT 1,
    node_count        INT         NOT NULL DEFAULT 0,
    component_scores  JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (computed_at)
);

CREATE INDEX IF NOT EXISTS idx_org_health_computed_at
    ON org_health_scores (computed_at DESC);

COMMENT ON TABLE org_health_scores IS
    'Weekly composite org health score (0–100). Higher = healthier. '
    'component_scores JSONB: {silo, spof, entropy, frag} each in [0,1].';
