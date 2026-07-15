-- Migration 028: Registry Engine Stage 1 (RE1) — foundations
-- Additive only. No behavior change. No existing column is altered or dropped.
--
-- Scope (per docs/architecture/registry engine unified architecture, Stage 1):
--   - Registry Passport identity columns on companies (public_id, legal_name,
--     operating_name, business_number, registry_status, verification_level)
--   - Registry Confidence classification column on companies (registry_confidence)
--     (Registry Layer / Market Segment / anchor_score are explicitly deferred to
--     the later coverage/curation stage — not part of Stage 1)
--   - registry_pin table (schema only; not wired into any live path yet)
--
-- Explicitly NOT part of this migration (reuse, not duplicate):
--   - Evidence Link: reuses the existing company_registry_links table (migration 016).
--   - Registry Audit: reuses the existing kg_engine_decision_records table (migration 026);
--     its decision/source_path/metadata_json columns already cover the Engine's shadow
--     decision logging needs.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS public_id VARCHAR(20);

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS legal_name VARCHAR(300) NOT NULL DEFAULT '';

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS operating_name VARCHAR(300) NOT NULL DEFAULT '';

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS business_number VARCHAR(30) NOT NULL DEFAULT '';

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS registry_status VARCHAR(20) NOT NULL DEFAULT 'active';

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS verification_level VARCHAR(30) NOT NULL DEFAULT 'pending';

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS registry_confidence VARCHAR(20) NOT NULL DEFAULT 'unverified';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_registry_status'
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT ck_companies_registry_status
            CHECK (registry_status IN ('active', 'merged', 'excluded', 'retired'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_verification_level'
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT ck_companies_verification_level
            CHECK (verification_level IN (
                'pending', 'orgbook_matched', 'odb_matched',
                'manually_verified', 'disputed', 'rejected'
            ));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_registry_confidence'
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT ck_companies_registry_confidence
            CHECK (registry_confidence IN ('verified', 'high', 'medium', 'low', 'unverified'));
    END IF;
END $$;

-- Backfill: assign a permanent public_id to every ELIGIBLE existing company
-- that doesn't already have one. Deterministic, id-ordered (gaps in
-- companies.id are irrelevant to ROW_NUMBER's dense ranking). Pure data
-- population — no behavior change; nothing reads this column yet.
--
-- Eligibility (must match db/company_canonical_constants.py's entity_role
-- vocabulary exactly):
--   entity_role IN ('canonical', 'standalone')  -- see legacy policy note below
--   AND registry_status = 'active'
--
-- 'canonical' is the obvious eligible case — it holds the Passport.
--
-- LEGACY POLICY FOR 'standalone': under the pre-Stage-3 architecture,
-- 'standalone' is the default entity_role for a company that has not yet
-- been run through explicit canonical-merge promotion (company_canonical_
-- merge.py) — it is not an alias of anything and today functions as its
-- own distinct identity in practice, even though it hasn't been formally
-- promoted to 'canonical'. Most existing companies are 'standalone', not
-- 'canonical', so treating only 'canonical' as eligible would backfill
-- almost nothing. This is a deliberate, temporary compatibility decision,
-- not a permanent architectural stance: once Stage 3 (registry-first
-- CREATE, ADR-10) and Stage 4 (Enforce Merge, ADR-11) are live, 'standalone'
-- may cease to be a legitimate terminal state, and this eligibility policy
-- will need to be revisited then.
--
-- Explicitly INELIGIBLE (must remain public_id IS NULL):
--   'applicant_alias' (ENTITY_ROLE_APPLICANT_ALIAS) — an alias, not a
--     distinct identity; resolves via canonical_company_id, never gets its
--     own permanent id.
--   'probable_person' (ENTITY_ROLE_PROBABLE_PERSON) — not a company at all
--     (WP3 skip case).
--   Any registry_status other than 'active' (merged/excluded/retired) — a
--     lifecycle-terminal row is not eligible for fresh identity assignment
--     at backfill time, even if its entity_role would otherwise qualify.
--
-- Safe to re-run under any of: a clean first run, a full re-run after
-- everything eligible is already assigned (no-op, since the WHERE clause
-- matches nothing further), or a partial/mixed state (some companies
-- already carry a public_id — from a prior partial run or from organic
-- company growth between runs — some still NULL). New sequence numbers
-- always continue from the current max already-assigned suffix rather
-- than restarting at 1, so they can never collide with an already-issued
-- value; existing public_id values are never read into the UPDATE's
-- target set, so they are never touched. A transaction-scoped advisory
-- lock serializes concurrent executions so two simultaneous runs cannot
-- race on computing overlapping sequence ranges.
-- BEGIN BACKFILL
DO $$
DECLARE
    max_existing_seq BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('registry_engine_public_id_backfill')::bigint);

    SELECT COALESCE(MAX(substring(public_id FROM 4)::bigint), 0)
    INTO max_existing_seq
    FROM companies
    WHERE public_id ~ '^TS-[0-9]{8,}$';

    WITH numbered AS (
        SELECT id, max_existing_seq + ROW_NUMBER() OVER (ORDER BY id) AS seq
        FROM companies
        WHERE public_id IS NULL
          AND entity_role IN ('canonical', 'standalone')
          AND registry_status = 'active'
    )
    UPDATE companies
    SET public_id = 'TS-' || LPAD(numbered.seq::text, 8, '0')
    FROM numbered
    WHERE companies.id = numbered.id;
END $$;
-- END BACKFILL

CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_public_id
    ON companies (public_id)
    WHERE public_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_companies_business_number
    ON companies (business_number)
    WHERE business_number <> '';

-- registry_pin: manual override table for future Registry Layer / anchor
-- candidacy pinning (renamed out of the "anchor" namespace to avoid colliding
-- with the Registry Constitution's own "anchor" classification concept).
-- Schema only in Stage 1 — no code path reads or writes this table yet.
CREATE TABLE IF NOT EXISTS registry_pin (
    id              BIGSERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    pin_key         VARCHAR(300) NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    created_by      VARCHAR(100) NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_registry_pin_key UNIQUE (pin_key)
);

CREATE INDEX IF NOT EXISTS ix_registry_pin_company_id
    ON registry_pin (company_id);
