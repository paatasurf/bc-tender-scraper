-- Add permit company resolution columns for KG Phase 1/2 dual-write support.
-- These columns store the canonical company assigned to a permit and the
-- confidence/method used to resolve it.

ALTER TABLE permits
    ADD COLUMN IF NOT EXISTS company_id INTEGER,
    ADD COLUMN IF NOT EXISTS canonical_merge_confidence REAL,
    ADD COLUMN IF NOT EXISTS canonical_merge_method VARCHAR(50) NOT NULL DEFAULT '';

-- Reference company identity used during resolution.
CREATE INDEX IF NOT EXISTS ix_permits_company_id ON permits (company_id);
