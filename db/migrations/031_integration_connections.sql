-- Migration 031: Integration Connections (AI-Workforce PR1 -- model +
-- connector contract only, per the Nango-adoption architecture review).
--
-- Additive only. Not wired into db.connection._run_migrations()/init_db(),
-- and not yet applied anywhere (no apply script exists in this PR; see
-- the PR description). "Not wired to automatic execution and not yet
-- applied" is the precise status -- this migration COULD in principle be
-- applied by hand with a raw SQL client, so "impossible to apply" would
-- overstate the guarantee; what IS guaranteed is that nothing in this
-- codebase runs it automatically.
--
-- Correction from review: integration_connections was ORIGINALLY mapped
-- as a db.models ORM class (a Base subclass). Empirical testing against
-- a disposable local Postgres proved init_db()'s unconditional
-- Base.metadata.create_all(bind=engine) auto-creates ANY Base subclass --
-- which would have silently created this table on every environment's
-- next init_db() run, contradicting the "stays inert" claim entirely.
-- Fixed by moving the Python-side definition to a Core Table on its own
-- private MetaData (db/integration_connections_tables.py), mirroring the
-- same precedent as e.g. the company-enrichment schema work's own fix for
-- this exact problem -- never a Base subclass for a staged-migration
-- table that must remain genuinely inert.
--
-- This table alone is created by this PR. actions / action_approvals /
-- action_events do NOT exist yet and are explicitly out of scope here --
-- they are the next PR (the executor), per the corrected PR plan.
--
-- Design notes (from architecture review corrections):
--   * nango_connection_id is stored as an opaque TEXT value -- no UUID
--     format is assumed or validated.
--   * Uniqueness is scoped to the documented identity triple Nango itself
--     uses: (environment, provider config, connection id) -- NOT a bare
--     global uniqueness on nango_connection_id alone.
--   * company_id is this repo's own authorization anchor. Nango's
--     connection "tags" are never read as a permission check anywhere in
--     this schema or the code that will query it.
--   * external_org_id captures the TARGET system's own org/workspace/site
--     identity (e.g. a Jira Cloud site id) -- distinct from both
--     nango_connection_id and company_id.
--   * allowed_projects is an explicit allow-list (JSONB array) of
--     projects/boards this connection may act on. Semantics, fixed by
--     review: allowed_projects = '[]' means NO project is authorized --
--     a deny-all default, never "unrestricted". An approved action's
--     target must be checked against this list by application code; this
--     migration only guarantees the column HOLDS a JSON array (see the
--     CHECK constraint below) -- per-element shape validation and the
--     actual allow-list enforcement belong to the executor PR, where
--     actions actually target a project.

CREATE TABLE IF NOT EXISTS integration_connections (
    id                          SERIAL PRIMARY KEY,
    company_id                  INTEGER NOT NULL REFERENCES companies(id),
    provider                    VARCHAR(50)  NOT NULL,
    nango_environment           VARCHAR(20)  NOT NULL,
    nango_provider_config_key   VARCHAR(100) NOT NULL,
    nango_connection_id         TEXT         NOT NULL,
    external_org_id             VARCHAR(200) NOT NULL,
    allowed_projects            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status                      VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    revoked_at                  TIMESTAMPTZ,
    CONSTRAINT ck_integration_connections_status
        CHECK (status IN ('active', 'revoked')),
    CONSTRAINT ck_integration_connections_revoked_at_coherent
        CHECK (
            (status = 'active' AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL)
        ),
    -- allowed_projects must always hold a JSON ARRAY (including the
    -- empty array, the deny-all default) -- never an object, scalar, or
    -- SQL NULL masquerading as "no restriction" by accident.
    CONSTRAINT ck_integration_connections_allowed_projects_is_array
        CHECK (jsonb_typeof(allowed_projects) = 'array')
);

-- The documented Nango identity scope -- NOT a bare unique on
-- nango_connection_id alone (that field's uniqueness is only guaranteed
-- within one environment + provider config, per Nango's own data model).
CREATE UNIQUE INDEX IF NOT EXISTS ux_integration_connections_nango_identity
    ON integration_connections (
        nango_environment, nango_provider_config_key, nango_connection_id
    );

CREATE INDEX IF NOT EXISTS ix_integration_connections_company_id
    ON integration_connections (company_id);

-- Fast "does this company have an active connection to this provider"
-- lookup -- the first authorization check the (future) executor PR will
-- always need before touching anything else.
CREATE INDEX IF NOT EXISTS ix_integration_connections_company_provider_active
    ON integration_connections (company_id, provider)
    WHERE status = 'active';
