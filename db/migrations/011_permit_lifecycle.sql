-- Migration 011: permit lifecycle schema (Permit Lifecycle Phase 2)
-- Additive only. Neutral backfill: all rows active / is_active=true until resolver runs.

ALTER TABLE permits
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(20) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS lifecycle_status_override VARCHAR(20),
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS source_status_raw VARCHAR(100) NOT NULL DEFAULT '';

UPDATE permits
SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
    is_active = COALESCE(is_active, true)
WHERE lifecycle_status IS NULL
   OR is_active IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_permits_lifecycle_status') THEN
        ALTER TABLE permits ADD CONSTRAINT ck_permits_lifecycle_status
            CHECK (lifecycle_status IN (
                'active', 'completed', 'cancelled', 'stale', 'unknown'
            ));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_permits_lifecycle_status ON permits (lifecycle_status);
CREATE INDEX IF NOT EXISTS ix_permits_is_active ON permits (is_active) WHERE is_active = true;
