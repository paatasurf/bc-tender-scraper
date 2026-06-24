-- Phase 4A: Vancouver permit application metadata for early-signal ingest.

ALTER TABLE permits ADD COLUMN IF NOT EXISTS application_date VARCHAR(20) DEFAULT '';
ALTER TABLE permits ADD COLUMN IF NOT EXISTS contractor VARCHAR(300) DEFAULT '';
ALTER TABLE permits ADD COLUMN IF NOT EXISTS local_area VARCHAR(100) DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_permits_application_date
    ON permits (application_date)
    WHERE application_date <> '';

CREATE INDEX IF NOT EXISTS ix_permits_local_area
    ON permits (local_area)
    WHERE local_area <> '';
