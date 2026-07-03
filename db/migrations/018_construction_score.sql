-- Migration 018: first-class construction_score for query/sort/filter

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS construction_score INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_companies_construction_score
    ON companies (construction_score DESC);
