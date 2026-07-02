-- Migration 009: tender presence tracking (P1-02)
-- first_seen_at  — set once on INSERT, never overwritten
-- last_seen_at   — refreshed whenever a tender appears in a successful import
-- updated_at     — refreshed only when tracked content fields change

ALTER TABLE tenders
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE commercial_tenders
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE arch_tenders
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- Backfill from scraped_at so existing rows keep a sensible history anchor.
UPDATE tenders
SET first_seen_at = COALESCE(first_seen_at, scraped_at),
    last_seen_at = COALESCE(last_seen_at, scraped_at),
    updated_at = COALESCE(updated_at, scraped_at)
WHERE first_seen_at IS NULL OR last_seen_at IS NULL OR updated_at IS NULL;

UPDATE commercial_tenders
SET first_seen_at = COALESCE(first_seen_at, scraped_at),
    last_seen_at = COALESCE(last_seen_at, scraped_at),
    updated_at = COALESCE(updated_at, scraped_at)
WHERE first_seen_at IS NULL OR last_seen_at IS NULL OR updated_at IS NULL;

UPDATE arch_tenders
SET first_seen_at = COALESCE(first_seen_at, scraped_at),
    last_seen_at = COALESCE(last_seen_at, scraped_at),
    updated_at = COALESCE(updated_at, scraped_at)
WHERE first_seen_at IS NULL OR last_seen_at IS NULL OR updated_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_tenders_last_seen_at ON tenders (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_commercial_tenders_last_seen_at ON commercial_tenders (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_arch_tenders_last_seen_at ON arch_tenders (last_seen_at DESC);
