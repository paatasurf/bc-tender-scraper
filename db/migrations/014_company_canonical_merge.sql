-- Migration 014: canonical company merge foundation
-- Additive only. Never deletes permits or company rows.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS display_name VARCHAR(300) DEFAULT '',
    ADD COLUMN IF NOT EXISTS entity_role VARCHAR(30) NOT NULL DEFAULT 'standalone',
    ADD COLUMN IF NOT EXISTS canonical_company_id INTEGER REFERENCES companies(id),
    ADD COLUMN IF NOT EXISTS applicant_signatory VARCHAR(300) DEFAULT '',
    ADD COLUMN IF NOT EXISTS canonical_merge_confidence FLOAT,
    ADD COLUMN IF NOT EXISTS canonical_merge_method VARCHAR(50) DEFAULT '';

ALTER TABLE permits
    ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id),
    ADD COLUMN IF NOT EXISTS canonical_merge_confidence FLOAT,
    ADD COLUMN IF NOT EXISTS canonical_merge_method VARCHAR(50) DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_companies_entity_role ON companies (entity_role);
CREATE INDEX IF NOT EXISTS ix_companies_canonical_company_id ON companies (canonical_company_id)
    WHERE canonical_company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_companies_display_name ON companies (display_name)
    WHERE display_name <> '';
CREATE INDEX IF NOT EXISTS ix_permits_company_id ON permits (company_id)
    WHERE company_id IS NOT NULL;

ALTER TABLE companies DROP CONSTRAINT IF EXISTS ck_companies_entity_role;
ALTER TABLE companies ADD CONSTRAINT ck_companies_entity_role
    CHECK (entity_role IN ('canonical', 'applicant_alias', 'standalone', 'probable_person'));

CREATE TABLE IF NOT EXISTS company_canonical_merge_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL DEFAULT 'planned',
    dry_run         BOOLEAN NOT NULL DEFAULT true,
    report_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary_json    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS company_canonical_merge_rollback (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES company_canonical_merge_runs(id) ON DELETE CASCADE,
    entity_type     VARCHAR(30) NOT NULL,
    entity_id       INTEGER NOT NULL,
    before_json     JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_company_canonical_merge_rollback_run_id
    ON company_canonical_merge_rollback (run_id);

CREATE TABLE IF NOT EXISTS company_applicant_aliases (
    id                      SERIAL PRIMARY KEY,
    canonical_company_id    INTEGER NOT NULL REFERENCES companies(id),
    alias_company_id        INTEGER NOT NULL REFERENCES companies(id),
    applicant_name_raw      VARCHAR(300) NOT NULL,
    signatory_name          VARCHAR(300) DEFAULT '',
    merge_run_id            INTEGER REFERENCES company_canonical_merge_runs(id),
    confidence              FLOAT NOT NULL,
    merge_method            VARCHAR(50) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_company_applicant_aliases_alias UNIQUE (alias_company_id)
);

CREATE INDEX IF NOT EXISTS ix_company_applicant_aliases_canonical
    ON company_applicant_aliases (canonical_company_id);
