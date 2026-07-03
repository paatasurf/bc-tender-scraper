-- Migration 016: registry verification layer (ODB reference + company links)
-- Read-only reference data; never mutates canonical company rows.

CREATE TABLE IF NOT EXISTS odbus_reference (
    odbus_idx           TEXT PRIMARY KEY,
    business_name       VARCHAR(500) NOT NULL DEFAULT '',
    alt_business_name   VARCHAR(500) NOT NULL DEFAULT '',
    normalized_name     VARCHAR(300) NOT NULL DEFAULT '',
    city                VARCHAR(100) NOT NULL DEFAULT '',
    normalized_city     VARCHAR(100) NOT NULL DEFAULT '',
    province            VARCHAR(10) NOT NULL DEFAULT '',
    status              VARCHAR(50) NOT NULL DEFAULT '',
    derived_naics       VARCHAR(10) NOT NULL DEFAULT '',
    source_naics        VARCHAR(20) NOT NULL DEFAULT '',
    licence_number      VARCHAR(100) NOT NULL DEFAULT '',
    business_id_no      VARCHAR(100) NOT NULL DEFAULT '',
    provider            VARCHAR(200) NOT NULL DEFAULT '',
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    imported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_odbus_reference_name_city
    ON odbus_reference (normalized_name, normalized_city, province);
CREATE INDEX IF NOT EXISTS ix_odbus_reference_name_province
    ON odbus_reference (normalized_name, province);
CREATE INDEX IF NOT EXISTS ix_odbus_reference_province
    ON odbus_reference (province);

CREATE TABLE IF NOT EXISTS company_registry_links (
    id                      SERIAL PRIMARY KEY,
    company_id              INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    source                  VARCHAR(30) NOT NULL,
    external_id             TEXT NOT NULL,
    match_tier              VARCHAR(5) NOT NULL,
    confidence              FLOAT NOT NULL,
    verification_status     VARCHAR(30) NOT NULL,
    multi_location          BOOLEAN NOT NULL DEFAULT false,
    metadata_json           JSONB NOT NULL DEFAULT '{}'::jsonb,
    linked_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_company_registry_links_source_external UNIQUE (source, external_id),
    CONSTRAINT uq_company_registry_links_company_source_external UNIQUE (company_id, source, external_id)
);

CREATE INDEX IF NOT EXISTS ix_company_registry_links_company_id
    ON company_registry_links (company_id);
CREATE INDEX IF NOT EXISTS ix_company_registry_links_source
    ON company_registry_links (source);
CREATE INDEX IF NOT EXISTS ix_company_registry_links_verification_status
    ON company_registry_links (verification_status);
