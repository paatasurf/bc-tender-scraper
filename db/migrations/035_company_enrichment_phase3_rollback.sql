-- Rollback for migration 035: Company enrichment Phase 3 provenance/
-- verification schema. Drops everything migration 035 added and nothing
-- else -- migration 034's own tables/columns/indexes/constraints are
-- never touched.
--
-- Order matters (docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2.4,
-- confirmed empirically in docs/COMPANY_CONTACT_PROVIDER_PHASE3_SCHEMA_REVIEW.md
-- S7.4): dropping a column a CHECK constraint depends on auto-cascades
-- that constraint's removal, but DROP FUNCTION while a CHECK constraint
-- still calls it fails loudly (DependentObjectsStillExist) -- so
-- constraints/columns that reference the function must go BEFORE the
-- function itself, every statement using IF EXISTS so the whole sequence
-- is safely rerunnable (confirmed empirically: running it twice in a row,
-- second run a clean no-op with no error).
--
-- Every ALTER TABLE below uses "ALTER TABLE IF EXISTS", not just
-- "DROP COLUMN/CONSTRAINT IF EXISTS" on an unconditional ALTER TABLE --
-- confirmed empirically that this rollback must also stay a safe no-op
-- when the underlying table doesn't exist at all (e.g. migration 034
-- itself was rolled back first, or never applied) -- without the
-- table-level IF EXISTS, "ALTER TABLE company_enrichment_jobs DROP
-- CONSTRAINT IF EXISTS ..." still fails outright with "relation
-- company_enrichment_jobs does not exist" the moment the table itself is
-- gone, regardless of the column/constraint-level IF EXISTS. This is a
-- real gap the design doc's own empirical testing (schema review S7.4)
-- never exercised, since it always ran rollback against the
-- fully-applied Phase 3 shape.

ALTER TABLE IF EXISTS company_enrichment_jobs
    DROP CONSTRAINT IF EXISTS ck_company_enrichment_jobs_field_attempt_log_shape;

ALTER TABLE IF EXISTS company_enrichment_jobs
    DROP COLUMN IF EXISTS field_attempt_log;

DROP FUNCTION IF EXISTS company_enrichment_validate_field_attempt_log(jsonb);

ALTER TABLE IF EXISTS company_enrichment_fields
    DROP CONSTRAINT IF EXISTS ck_company_enrichment_fields_verified_evidence;

ALTER TABLE IF EXISTS company_enrichment_fields
    DROP COLUMN IF EXISTS source_url,
    DROP COLUMN IF EXISTS raw_value,
    DROP COLUMN IF EXISTS extraction_method,
    DROP COLUMN IF EXISTS verified_at,
    DROP COLUMN IF EXISTS verified_by,
    DROP COLUMN IF EXISTS verification_source_url;
