-- Migration 013: Google enrichment infrastructure (Phase 0)
-- Additive only. No column renames or removals.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS google_place_id            VARCHAR(200),
    ADD COLUMN IF NOT EXISTS google_business_category  VARCHAR(200) DEFAULT '',
    ADD COLUMN IF NOT EXISTS google_maps_url            VARCHAR(500) DEFAULT '',
    ADD COLUMN IF NOT EXISTS google_business_status     VARCHAR(50)  DEFAULT '',
    ADD COLUMN IF NOT EXISTS google_website             VARCHAR(500) DEFAULT '',
    ADD COLUMN IF NOT EXISTS google_last_updated        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS google_last_seen            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS google_match_confidence     FLOAT,
    ADD COLUMN IF NOT EXISTS google_enrichment_status    VARCHAR(30)  DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS google_query_used           VARCHAR(500) DEFAULT '',
    ADD COLUMN IF NOT EXISTS website                    VARCHAR(500) DEFAULT '',
    ADD COLUMN IF NOT EXISTS google_lat                   FLOAT,
    ADD COLUMN IF NOT EXISTS google_lng                   FLOAT;

CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_google_place_id
    ON companies (google_place_id)
    WHERE google_place_id IS NOT NULL AND google_place_id <> '';

CREATE INDEX IF NOT EXISTS ix_companies_google_enrichment_eligible
    ON companies (lifecycle_status, is_operating, google_last_updated)
    WHERE lifecycle_status = 'active' AND is_operating = true;

CREATE TABLE IF NOT EXISTS google_enrichment_logs (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    run_id              VARCHAR(36) NOT NULL,
    attempted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_used          VARCHAR(500) NOT NULL DEFAULT '',
    provider            VARCHAR(30)  NOT NULL,
    status              VARCHAR(30)  NOT NULL,
    match_confidence    FLOAT,
    google_place_id     VARCHAR(200),
    candidate_count     INTEGER DEFAULT 0,
    candidate_snapshot  JSONB,
    error_message       TEXT DEFAULT '',
    latency_ms          INTEGER,
    external_run_id     VARCHAR(100) DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_google_enrichment_logs_company
    ON google_enrichment_logs (company_id, attempted_at DESC);

CREATE INDEX IF NOT EXISTS ix_google_enrichment_logs_run
    ON google_enrichment_logs (run_id);

CREATE INDEX IF NOT EXISTS ix_google_enrichment_logs_attempted
    ON google_enrichment_logs (attempted_at DESC);

CREATE TABLE IF NOT EXISTS google_enrichment_reviews (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    run_id              VARCHAR(36) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_used          VARCHAR(500) NOT NULL DEFAULT '',
    match_confidence    FLOAT NOT NULL,
    candidate_snapshot  JSONB NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    reviewed_at         TIMESTAMPTZ,
    reviewed_by         VARCHAR(100) DEFAULT '',
    review_notes        TEXT DEFAULT '',
    chosen_place_id     VARCHAR(200)
);

CREATE INDEX IF NOT EXISTS ix_google_enrichment_reviews_pending
    ON google_enrichment_reviews (status, created_at DESC)
    WHERE status = 'pending';
