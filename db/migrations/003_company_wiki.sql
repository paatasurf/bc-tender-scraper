-- Migration 003: company_wiki table
-- Stores AI-generated company intelligence profiles keyed by (company_id, company_kind).

CREATE TABLE IF NOT EXISTS company_wiki (
    id                  SERIAL PRIMARY KEY,
    company_id          INTEGER NOT NULL,
    company_kind        VARCHAR(20) NOT NULL DEFAULT 'construction',
    company_name        VARCHAR(300) NOT NULL DEFAULT '',
    wiki_markdown       TEXT NOT NULL DEFAULT '',
    -- structured sections extracted from the AI narrative
    summary             TEXT NOT NULL DEFAULT '',
    specializations     TEXT NOT NULL DEFAULT '',
    market_position     TEXT NOT NULL DEFAULT '',
    geographic_focus    TEXT NOT NULL DEFAULT '',
    competitive_profile TEXT NOT NULL DEFAULT '',
    data_snapshot       JSONB,          -- raw numbers used to generate (permits, awards, etc.)
    model_used          VARCHAR(80) NOT NULL DEFAULT '',
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_company_wiki_company_kind
    ON company_wiki (company_id, company_kind);

CREATE INDEX IF NOT EXISTS ix_company_wiki_generated_at
    ON company_wiki (generated_at DESC);

CREATE INDEX IF NOT EXISTS ix_company_wiki_company_kind_col
    ON company_wiki (company_kind);
