-- Migration 024: sector inference confidence on companies (CIP backfill)

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS sector_confidence VARCHAR(10) DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_companies_sector_confidence
    ON companies (sector_confidence)
    WHERE sector_confidence <> '';
