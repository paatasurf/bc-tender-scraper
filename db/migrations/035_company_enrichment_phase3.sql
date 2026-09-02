-- Migration 035: Company enrichment Phase 3 provenance/verification schema
-- (docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2, Class D DDL).
--
-- Additive only. Alters the two existing tables migration 034 created --
-- company_enrichment_fields (6 new columns + 1 CHECK constraint) and
-- company_enrichment_jobs (1 new JSONB column + 1 validation function + 1
-- CHECK constraint) -- touches no other table (including companies).
--
-- NOT wired into db.connection._run_migrations() / init_db(), exactly like
-- migration 034 -- this schema stays inert everywhere (local, staging,
-- production) until an operator explicitly runs
-- scripts/run_company_enrichment_phase3_migration.py --apply. Applying
-- this migration does NOT enable ENRICHMENT_ENABLED, does NOT wire
-- WebsiteContactProvider into _default_providers(), and does NOT write any
-- application data -- it only makes the columns/constraint/function exist
-- so a future, separately-authorized change can start using them.
--
-- Requires migration 034 to already be applied (ALTER TABLE ... ADD COLUMN
-- against a table that doesn't exist fails loudly with "relation ... does
-- not exist" -- a correct, fail-explicit outcome, not guarded against
-- specially here; db/company_enrichment_migration.py's
-- company_enrichment_phase3_apply_readiness() checks this precondition
-- before the apply script ever reaches this SQL).
--
-- company_enrichment_validate_field_attempt_log(): a small IMMUTABLE
-- PL/pgSQL function, not inline SQL, because Postgres CHECK constraints
-- cannot directly contain a subquery or a set-returning function call
-- (jsonb_array_elements cannot be used inline inside a CHECK expression).
-- Bounds (max 20 entries, field <= 50 chars, provider <= 30 chars, reason
-- <= 200 chars) match company_enrichment_fields.field_name/source's own
-- declared widths exactly, so a log entry can never describe a
-- field/provider combination that couldn't itself exist in that table.
-- JSONB itself has no field-level length limit (TOAST-backed) -- these
-- bounds are enforced by the function, not by column width, so a
-- rejection is always a loud INSERT/UPDATE failure, never a silent
-- truncation (design doc S2.1.1; the prior VARCHAR(80)[] design this
-- replaces was empirically confirmed to silently truncate an over-length
-- entry under this repo's own Column(ARRAY(String(N))) convention).
CREATE OR REPLACE FUNCTION company_enrichment_validate_field_attempt_log(log jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $func$
DECLARE
    entry jsonb;
BEGIN
    IF jsonb_typeof(log) IS DISTINCT FROM 'array' THEN
        RETURN FALSE;
    END IF;
    IF jsonb_array_length(log) > 20 THEN
        RETURN FALSE;
    END IF;
    FOR entry IN SELECT * FROM jsonb_array_elements(log)
    LOOP
        IF jsonb_typeof(entry) IS DISTINCT FROM 'object' THEN
            RETURN FALSE;
        END IF;
        IF NOT (entry ? 'field' AND entry ? 'status' AND entry ? 'provider' AND entry ? 'attempted_at') THEN
            RETURN FALSE;
        END IF;
        IF jsonb_typeof(entry->'field') IS DISTINCT FROM 'string'
           OR jsonb_typeof(entry->'status') IS DISTINCT FROM 'string'
           OR jsonb_typeof(entry->'provider') IS DISTINCT FROM 'string'
           OR jsonb_typeof(entry->'attempted_at') IS DISTINCT FROM 'string'
        THEN
            RETURN FALSE;
        END IF;
        IF (entry->>'status') NOT IN ('no_match', 'fetch_error', 'not_attempted') THEN
            RETURN FALSE;
        END IF;
        IF length(entry->>'field') > 50 OR length(entry->>'provider') > 30 THEN
            RETURN FALSE;
        END IF;
        IF entry ? 'reason' AND jsonb_typeof(entry->'reason') NOT IN ('string', 'null') THEN
            RETURN FALSE;
        END IF;
        IF entry ? 'reason' AND jsonb_typeof(entry->'reason') = 'string'
           AND length(entry->>'reason') > 200 THEN
            RETURN FALSE;
        END IF;
    END LOOP;
    RETURN TRUE;
END;
$func$;

-- field_attempt_log: distinguishes not_attempted / fetch_error / no_match
-- (which never produce a company_enrichment_fields row) from row-absence
-- being silently misread as no_match (design doc S2.1). Candidate outcomes
-- (unverified_candidate / verified_candidate) are deliberately never
-- logged here -- that's what the fields-table row itself already states.
ALTER TABLE company_enrichment_jobs
    ADD COLUMN IF NOT EXISTS field_attempt_log JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ALTER TABLE ... ADD CONSTRAINT has never supported IF NOT EXISTS (unlike
-- ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS) -- confirmed
-- empirically in docs/COMPANY_CONTACT_PROVIDER_PHASE3_SCHEMA_REVIEW.md S2:
-- "syntax error at or near EXISTS". This DO $$ guard is the standard
-- Postgres idiom, confirmed idempotent (run twice in the same session,
-- second run a clean no-op). It checks existence BY NAME ONLY -- it
-- cannot detect a pre-existing, same-named, wrong-shaped constraint (the
-- exact blind spot CREATE INDEX IF NOT EXISTS already has elsewhere in
-- this schema); db/company_enrichment_migration.py's
-- company_enrichment_phase3_apply_readiness() is what actually verifies
-- the constraint's real shape via pg_get_constraintdef, not this guard.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_company_enrichment_jobs_field_attempt_log_shape'
    ) THEN
        ALTER TABLE company_enrichment_jobs
            ADD CONSTRAINT ck_company_enrichment_jobs_field_attempt_log_shape
            CHECK (company_enrichment_validate_field_attempt_log(field_attempt_log));
    END IF;
END $$;

-- Provenance/verification columns (design doc S2.2, "Option A", the
-- recommended and only live option -- Option B, a companion JSON-blob
-- row, was reviewed and rejected as un-queryable and unenforceable).
-- source_url/raw_value/extraction_method are populated by a provider on
-- every candidate write; verified_at/verified_by/verification_source_url
-- are populated ONLY by the manual review workflow (design doc S5),
-- never by any provider or orchestrator code path -- ProviderFact and
-- write_enrichment_facts() (pipeline/company_enrichment/) are unchanged
-- by this migration and remain structurally incapable of setting them.
ALTER TABLE company_enrichment_fields
    ADD COLUMN IF NOT EXISTS source_url             TEXT,
    ADD COLUMN IF NOT EXISTS raw_value               TEXT,
    ADD COLUMN IF NOT EXISTS extraction_method       VARCHAR(30),
    ADD COLUMN IF NOT EXISTS verified_at             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS verified_by             VARCHAR(100),
    ADD COLUMN IF NOT EXISTS verification_source_url TEXT;

-- Makes it structurally impossible to set verified = TRUE without also
-- recording who did it, when, and what independent evidence they checked
-- (design doc S2.3 rule 4) -- a promotion missing any of the three fails
-- the UPDATE itself, not just a code review comment. Same DO $$ idiom and
-- same name-only blind spot as the constraint above (closed by the
-- readiness check, not this guard).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_company_enrichment_fields_verified_evidence'
    ) THEN
        ALTER TABLE company_enrichment_fields
            ADD CONSTRAINT ck_company_enrichment_fields_verified_evidence
            CHECK (NOT verified OR (
                verified_by IS NOT NULL
                AND verified_at IS NOT NULL
                AND verification_source_url IS NOT NULL
            ));
    END IF;
END $$;
