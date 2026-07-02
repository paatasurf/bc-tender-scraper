-- Migration 010: tender lifecycle schema foundation (P2-01)
-- Additive only. No classification. Existing tenders remain functionally open (is_open=true, lifecycle_status=active).

-- ---------------------------------------------------------------------------
-- tenders
-- ---------------------------------------------------------------------------
ALTER TABLE tenders
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS is_open BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS lifecycle_status_override VARCHAR(30),
    ADD COLUMN IF NOT EXISTS lifecycle_override_reason TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lifecycle_override_by VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS closing_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS awarded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS missing_from_source_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_status_raw TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_status_normalized VARCHAR(50) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS award_id INTEGER,
    ADD COLUMN IF NOT EXISTS award_match_confidence DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS addenda_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_addendum_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- commercial_tenders
-- ---------------------------------------------------------------------------
ALTER TABLE commercial_tenders
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS is_open BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS lifecycle_status_override VARCHAR(30),
    ADD COLUMN IF NOT EXISTS lifecycle_override_reason TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lifecycle_override_by VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS closing_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS awarded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS missing_from_source_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_status_raw TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_status_normalized VARCHAR(50) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS award_id INTEGER,
    ADD COLUMN IF NOT EXISTS award_match_confidence DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS addenda_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_addendum_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- arch_tenders
-- ---------------------------------------------------------------------------
ALTER TABLE arch_tenders
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS is_open BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS lifecycle_status_override VARCHAR(30),
    ADD COLUMN IF NOT EXISTS lifecycle_override_reason TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lifecycle_override_by VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS closing_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS awarded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS missing_from_source_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_status_raw TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_status_normalized VARCHAR(50) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS award_id INTEGER,
    ADD COLUMN IF NOT EXISTS award_match_confidence DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS addenda_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_addendum_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- Neutral backfill: preserve current open-market behavior until reconciliation (P2-03+)
-- ---------------------------------------------------------------------------
UPDATE tenders
SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
    is_open = COALESCE(is_open, true),
    missing_from_source_count = COALESCE(missing_from_source_count, 0),
    addenda_count = COALESCE(addenda_count, 0)
WHERE lifecycle_status IS NULL
   OR is_open IS NULL
   OR missing_from_source_count IS NULL
   OR addenda_count IS NULL;

UPDATE commercial_tenders
SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
    is_open = COALESCE(is_open, true),
    missing_from_source_count = COALESCE(missing_from_source_count, 0),
    addenda_count = COALESCE(addenda_count, 0)
WHERE lifecycle_status IS NULL
   OR is_open IS NULL
   OR missing_from_source_count IS NULL
   OR addenda_count IS NULL;

UPDATE arch_tenders
SET lifecycle_status = COALESCE(lifecycle_status, 'active'),
    is_open = COALESCE(is_open, true),
    missing_from_source_count = COALESCE(missing_from_source_count, 0),
    addenda_count = COALESCE(addenda_count, 0)
WHERE lifecycle_status IS NULL
   OR is_open IS NULL
   OR missing_from_source_count IS NULL
   OR addenda_count IS NULL;

-- ---------------------------------------------------------------------------
-- Indexes (concurrent-safe via IF NOT EXISTS; no table rewrites)
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_tenders_lifecycle_status ON tenders (lifecycle_status);
CREATE INDEX IF NOT EXISTS ix_tenders_is_open ON tenders (is_open) WHERE is_open = true;
CREATE INDEX IF NOT EXISTS ix_tenders_closing_at ON tenders (closing_at) WHERE closing_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_commercial_tenders_lifecycle_status ON commercial_tenders (lifecycle_status);
CREATE INDEX IF NOT EXISTS ix_commercial_tenders_is_open ON commercial_tenders (is_open) WHERE is_open = true;
CREATE INDEX IF NOT EXISTS ix_commercial_tenders_closing_at ON commercial_tenders (closing_at) WHERE closing_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_arch_tenders_lifecycle_status ON arch_tenders (lifecycle_status);
CREATE INDEX IF NOT EXISTS ix_arch_tenders_is_open ON arch_tenders (is_open) WHERE is_open = true;
CREATE INDEX IF NOT EXISTS ix_arch_tenders_closing_at ON arch_tenders (closing_at) WHERE closing_at IS NOT NULL;

-- Optional integrity check (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_tenders_lifecycle_status'
    ) THEN
        ALTER TABLE tenders ADD CONSTRAINT ck_tenders_lifecycle_status
            CHECK (lifecycle_status IN (
                'new', 'active', 'closing_soon', 'closed', 'awarded',
                'cancelled', 'outcome_unknown', 'archived'
            ));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_commercial_tenders_lifecycle_status'
    ) THEN
        ALTER TABLE commercial_tenders ADD CONSTRAINT ck_commercial_tenders_lifecycle_status
            CHECK (lifecycle_status IN (
                'new', 'active', 'closing_soon', 'closed', 'awarded',
                'cancelled', 'outcome_unknown', 'archived'
            ));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_arch_tenders_lifecycle_status'
    ) THEN
        ALTER TABLE arch_tenders ADD CONSTRAINT ck_arch_tenders_lifecycle_status
            CHECK (lifecycle_status IN (
                'new', 'active', 'closing_soon', 'closed', 'awarded',
                'cancelled', 'outcome_unknown', 'archived'
            ));
    END IF;
END $$;
