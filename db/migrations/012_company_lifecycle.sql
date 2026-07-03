-- Migration 012: company lifecycle schema (Company Lifecycle Phase 2)
-- Additive only. Neutral backfill: all rows active / is_operating=true until resolver runs.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS lifecycle_status_override VARCHAR(30),
    ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS is_operating BOOLEAN NOT NULL DEFAULT true;

UPDATE companies
SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
    is_operating = COALESCE(is_operating, true)
WHERE lifecycle_status IS NULL
   OR is_operating IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_lifecycle_status') THEN
        ALTER TABLE companies ADD CONSTRAINT ck_companies_lifecycle_status
            CHECK (lifecycle_status IN (
                'active', 'quiet', 'dormant', 'no_observable_activity'
            ));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_companies_lifecycle_status ON companies (lifecycle_status);
CREATE INDEX IF NOT EXISTS ix_companies_is_operating ON companies (is_operating) WHERE is_operating = true;
