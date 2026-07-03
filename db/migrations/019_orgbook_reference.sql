-- Migration 019: OrgBook BC reference data for Verification Hub

CREATE TABLE IF NOT EXISTS orgbook_reference (
    orgbook_id          TEXT PRIMARY KEY,
    legal_name          VARCHAR(500) NOT NULL DEFAULT '',
    dba_names           JSONB NOT NULL DEFAULT '[]'::jsonb,
    normalized_name     VARCHAR(300) NOT NULL DEFAULT '',
    business_number     VARCHAR(20) NOT NULL DEFAULT '',
    registry_id         VARCHAR(30) NOT NULL DEFAULT '',
    entity_type         VARCHAR(100) NOT NULL DEFAULT '',
    status              VARCHAR(50) NOT NULL DEFAULT '',
    city                VARCHAR(100) NOT NULL DEFAULT '',
    normalized_city     VARCHAR(100) NOT NULL DEFAULT '',
    province            VARCHAR(10) NOT NULL DEFAULT 'BC',
    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    imported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_orgbook_reference_name_city
    ON orgbook_reference (normalized_name, normalized_city);
CREATE INDEX IF NOT EXISTS ix_orgbook_reference_normalized_name
    ON orgbook_reference (normalized_name);
CREATE INDEX IF NOT EXISTS ix_orgbook_reference_business_number
    ON orgbook_reference (business_number)
    WHERE business_number <> '';
