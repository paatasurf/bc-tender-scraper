-- Migration 017: construction tier evidence breakdown (deterministic, no LLM)

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS construction_tier_json JSONB,
    ADD COLUMN IF NOT EXISTS construction_tier_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_companies_company_tier
    ON companies (company_tier)
    WHERE company_tier <> '';
