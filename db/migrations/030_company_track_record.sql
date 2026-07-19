-- Migration 030: Company track-record scorer schema foundation (PR-G2A).
-- Additive only. No existing column is altered or dropped. No indexes yet
-- -- no production consumer reads these columns. No data migration, no
-- backfill -- every existing row starts fully uncomputed (all four columns
-- NULL). Not wired into _run_migrations()/init_db() -- this schema stays
-- inert until an operator explicitly applies it, same pattern as migration
-- 029 (classification_claims).
--
-- Companies only -- arch_companies is intentionally untouched.
-- ai_reliability_score / ai_summary are untouched by this migration; the
-- deterministic scorer these columns will eventually back
-- (pipeline.scoring.company_track_record, PR-G1) is not wired to any
-- caller or to these columns yet.
--
-- Projection contract enforced by the CHECK constraints below:
--   * fully uncomputed: track_record_json, track_record_at,
--     track_record_version, and track_record_score are ALL NULL.
--   * computed: track_record_json, track_record_at, and
--     track_record_version are ALL NOT NULL. track_record_score MAY still
--     be NULL within this state -- it represents the scorer's "no core
--     evidence" result (PR-G1's score=None), which is a legitimate computed
--     outcome, not missing data.
--   * no other combination is valid -- a non-null score can never appear
--     without json/timestamp/version also being non-null, and a partial
--     computed state (e.g. json set but version NULL) is rejected.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS track_record_score INTEGER NULL;

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS track_record_json JSONB NULL;

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS track_record_at TIMESTAMPTZ NULL;

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS track_record_version VARCHAR(64) NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_companies_track_record_score_range'
          AND conrelid = 'companies'::regclass
          AND contype = 'c'
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT ck_companies_track_record_score_range
            CHECK (
                track_record_score IS NULL
                OR (track_record_score >= 0 AND track_record_score <= 100)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_companies_track_record_version_not_empty'
          AND conrelid = 'companies'::regclass
          AND contype = 'c'
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT ck_companies_track_record_version_not_empty
            CHECK (track_record_version IS NULL OR track_record_version <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_companies_track_record_state_coherent'
          AND conrelid = 'companies'::regclass
          AND contype = 'c'
    ) THEN
        ALTER TABLE companies
            ADD CONSTRAINT ck_companies_track_record_state_coherent
            CHECK (
                (
                    track_record_json IS NULL
                    AND track_record_at IS NULL
                    AND track_record_version IS NULL
                    AND track_record_score IS NULL
                )
                OR
                (
                    track_record_json IS NOT NULL
                    AND track_record_at IS NOT NULL
                    AND track_record_version IS NOT NULL
                )
            );
    END IF;
END $$;
