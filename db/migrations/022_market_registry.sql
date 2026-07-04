-- Migration 022: market_registry staging + odbus_reference batch versioning

-- odbus_reference: batch versioning (no hard deletes on import)
ALTER TABLE odbus_reference
    ADD COLUMN IF NOT EXISTS ingest_batch_id VARCHAR(36) NOT NULL DEFAULT 'legacy';

ALTER TABLE odbus_reference
    ADD COLUMN IF NOT EXISTS observation_status VARCHAR(20) NOT NULL DEFAULT 'active';

ALTER TABLE odbus_reference
    ADD COLUMN IF NOT EXISTS source_observed_at DATE;

UPDATE odbus_reference
SET observation_status = 'active'
WHERE observation_status IS NULL OR btrim(observation_status) = '';

CREATE INDEX IF NOT EXISTS ix_odbus_reference_batch
    ON odbus_reference (ingest_batch_id);

CREATE INDEX IF NOT EXISTS ix_odbus_reference_observation_status
    ON odbus_reference (observation_status);

CREATE INDEX IF NOT EXISTS ix_odbus_reference_active_province
    ON odbus_reference (province)
    WHERE observation_status = 'active';

-- market_registry staging table (Phase A observations)
CREATE TABLE IF NOT EXISTS market_registry (
    id                      BIGSERIAL PRIMARY KEY,
    source                  VARCHAR(40) NOT NULL,
    source_record_id        TEXT NOT NULL,
    feed_kind               VARCHAR(30) NOT NULL,
    promotion_status        VARCHAR(20) NOT NULL,
    source_confidence       CHAR(1) NOT NULL,
    original_name           VARCHAR(500) NOT NULL,
    normalized_name         VARCHAR(300) NOT NULL DEFAULT '',
    name_type               VARCHAR(20) NOT NULL DEFAULT 'unknown',
    city                    VARCHAR(100) NOT NULL DEFAULT '',
    normalized_city         VARCHAR(100) NOT NULL DEFAULT '',
    province                VARCHAR(10) NOT NULL DEFAULT 'BC',
    business_number         VARCHAR(30) NOT NULL DEFAULT '',
    licence_identifier      VARCHAR(100) NOT NULL DEFAULT '',
    website                 VARCHAR(500) NOT NULL DEFAULT '',
    registry_identifiers    JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    tenderscope_company_id  INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    odbus_idx               TEXT,
    seed_id                 VARCHAR(20) NOT NULL DEFAULT '',
    ingest_batch_id         VARCHAR(36) NOT NULL,
    source_observed_at      DATE,
    observation_status      VARCHAR(20) NOT NULL DEFAULT 'active',
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_market_registry_source
        CHECK (source IN (
            'enterprise_seed', 'odb_primary', 'odb_or_candidate', 'awards', 'permits'
        )),
    CONSTRAINT ck_market_registry_feed_kind
        CHECK (feed_kind IN (
            'forced_registry', 'core_registry', 'candidate_queue', 'evidence_only'
        )),
    CONSTRAINT ck_market_registry_promotion_status
        CHECK (promotion_status IN (
            'core', 'candidate', 'rejected', 'evidence_only'
        )),
    CONSTRAINT ck_market_registry_observation_status
        CHECK (observation_status IN ('active', 'inactive', 'superseded')),
    CONSTRAINT ck_market_registry_source_confidence
        CHECK (source_confidence IN ('A', 'B', 'C', 'D', 'E'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_registry_active_source_key
    ON market_registry (source, source_record_id)
    WHERE observation_status = 'active';

CREATE INDEX IF NOT EXISTS ix_market_registry_normalized_name_active
    ON market_registry (normalized_name)
    WHERE observation_status = 'active';

CREATE INDEX IF NOT EXISTS ix_market_registry_promotion_source
    ON market_registry (promotion_status, source);

CREATE INDEX IF NOT EXISTS ix_market_registry_ingest_batch
    ON market_registry (ingest_batch_id);

CREATE INDEX IF NOT EXISTS ix_market_registry_odbus_idx
    ON market_registry (odbus_idx)
    WHERE odbus_idx IS NOT NULL AND odbus_idx <> '';
