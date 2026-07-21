-- Migration 031: Surrey official source identity schema foundation (PR-EN1E-1).
--
-- Additive only. Adds one new, nullable column to permits: official_source_id,
-- intended to eventually hold the full current-format official source
-- identifier (e.g. Surrey's ArcGIS PermitNumber, 19-21 chars) for rows whose
-- existing permits.external_id already holds a shorter legacy canonical key
-- and therefore cannot safely be repointed at the new identifier without
-- breaking every existing join/dedupe key that already depends on it.
--
-- permits.external_id is NOT altered, renamed, or reinterpreted by this
-- migration. No existing row's data is touched -- every row starts with
-- official_source_id NULL.
--
-- Not wired into _run_migrations()/init_db() -- this schema stays inert
-- until an identity-bridge PR explicitly backfills it, same pattern as
-- migrations 029 (classification_claims) and 030 (company_track_record).
-- No importer, scraper, scheduler, or Company-linking logic reads or writes
-- this column yet.
--
-- The partial unique index below enforces at most one Permit per
-- (source, official_source_id) pair, but only among rows where
-- official_source_id is actually populated with a non-empty value --
-- NULL and '' are both excluded from the uniqueness check, so any number
-- of not-yet-bridged rows (the common case today) can freely coexist.

ALTER TABLE permits
    ADD COLUMN IF NOT EXISTS official_source_id VARCHAR(100) NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_permits_source_official_source_id
    ON permits (source, official_source_id)
    WHERE official_source_id IS NOT NULL AND official_source_id <> '';
