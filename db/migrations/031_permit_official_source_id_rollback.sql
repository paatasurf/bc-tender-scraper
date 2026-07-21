-- Rollback for migration 031 (Surrey official source identity schema
-- foundation).
--
-- Only safe to run while this column has no consumer -- i.e. before any
-- identity-bridge PR has backfilled it. As of this migration's own PR
-- (schema/ORM foundation only, no bridge, no importer, no scheduler wiring),
-- every row's official_source_id is guaranteed NULL, so this rollback is
-- side-effect-free. It performs no emptiness check of its own -- if a later
-- bridge PR has since populated this column, dropping it here would
-- silently discard that data, so do not run this file once a bridge PR has
-- merged without first confirming (e.g. via a fresh row count) that the
-- column is still empty.
--
-- Drop order is the exact reverse of the CREATE order in
-- 031_permit_official_source_id.sql.

DROP INDEX IF EXISTS ux_permits_source_official_source_id;

ALTER TABLE permits
    DROP COLUMN IF EXISTS official_source_id;
