-- Migration 020: construction score history (prepared for future trend analysis)

CREATE TABLE IF NOT EXISTS company_score_history (
    id                  SERIAL PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    construction_score  INTEGER NOT NULL,
    company_tier        VARCHAR(20) NOT NULL DEFAULT '',
    calculated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    algorithm_version   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_company_score_history_company_id
    ON company_score_history (company_id, calculated_at DESC);
CREATE INDEX IF NOT EXISTS ix_company_score_history_calculated_at
    ON company_score_history (calculated_at DESC);
CREATE INDEX IF NOT EXISTS ix_company_score_history_construction_score
    ON company_score_history (construction_score DESC);
