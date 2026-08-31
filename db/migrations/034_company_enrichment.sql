-- Migration 034: Company on-demand enrichment schema foundation (Phase 1
-- of docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md, Class D DDL).
--
-- Additive only. Creates two new tables; touches no existing table
-- (including companies -- no column is added there, no default changed).
--
-- company_enrichment_fields: per-field provenance for on-demand enrichment
-- (phone/address/website/etc.), one CURRENT row per (company_id,
-- field_name, source) -- superseded_at marks a row as no longer current
-- rather than deleting it, so the full history of what a provider claimed
-- and when is always recoverable (RFC S5/S12). Closes the gap RFC S1.3
-- identified in pipeline/google_enrichment/writer.py::CompanyGoogleWriter,
-- which unconditionally overwrites its allowlisted columns with no
-- provenance record and no verified-field protection.
--
-- company_enrichment_jobs: on-demand job lifecycle + in-flight dedup, one
-- row per run_id, keyed for dedup by company_id (NOT by (job_type, run_id)
-- the way ops_job_runs/pipeline_coordinator_runs are) -- RFC S7 step 3.
-- lease_expires_at follows the same heartbeat-renewed-TTL pattern
-- migration 032 (pipeline_coordinator_runs) and migration 033
-- (ops_job_runs) already established in production.
--
-- NOT wired into db.connection._run_migrations() / init_db() -- this
-- schema stays inert everywhere (local, staging, production) until an
-- operator explicitly runs scripts/run_company_enrichment_migration.py
-- --apply. The tables pipeline/company_enrichment/* reads/writes are
-- declared as plain SQLAlchemy Core Table objects
-- (db/company_enrichment_tables.py), not db.models ORM classes on Base --
-- Base.metadata.create_all(), which init_db() calls unconditionally on
-- every app startup/deploy, therefore can never auto-create this schema
-- either. Uses the same Class-D dry-run/apply/postcondition-verification
-- CLI pattern as migrations 032 and 033.
--
-- Phase 1 of the RFC's implementation task ships this schema only -- no
-- existing route, job, or scheduler is wired to call it in this PR. See
-- Phase 2 (orchestrator + gated /internal/enrichment/company/{id}/run
-- route) for the first real caller, itself gated behind
-- ENRICHMENT_ENABLED=false by default.
--
-- company_enrichment_jobs.status vocabulary (running/success/failed/
-- partial_success) mirrors pipeline/runs.py::_resolve_status()'s exact
-- committed_chunks/write_failures distinction, generalized to providers
-- (pipeline/company_enrichment/orchestrator.py::_resolve_cascade_status()):
-- success = every attempted provider ran cleanly (a clean no-match is
-- still success, per RFC golden case #9 -- "no match... never an
-- error"); partial_success = some providers errored/timed out but at
-- least one ran cleanly; failed = every attempted provider errored or
-- timed out, or the job's lease was reclaimed before it could report in.
--
-- CHECK constraints on status/trigger are a database-level backstop --
-- pipeline/company_enrichment/orchestrator.py already validates both in
-- Python before ever writing, but these constraints mean a direct/manual
-- SQL INSERT or UPDATE (bypassing that module entirely) can never leave
-- an invalid value in either column. Named explicitly so
-- db/company_enrichment_migration.py's schema-contract check can verify
-- their presence by name. field_name and source are intentionally left
-- unconstrained by CHECK (open-ended per RFC S5's own comment -- new
-- providers/fields are additive, not a fixed enum).

CREATE TABLE IF NOT EXISTS company_enrichment_fields (
    id             SERIAL PRIMARY KEY,
    company_id     INTEGER NOT NULL REFERENCES companies (id),
    field_name     VARCHAR(50) NOT NULL,
    value          TEXT NOT NULL,
    source         VARCHAR(30) NOT NULL,
    confidence     DOUBLE PRECISION,
    verified       BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at  TIMESTAMPTZ,
    run_id         VARCHAR(36)
);

-- One CURRENT row per (company, field, provider) -- a provider re-fetching
-- the same field must supersede its own prior row (UPDATE superseded_at),
-- never insert a second live row for the identical (company_id,
-- field_name, source) triple. Bugbot finding: this MUST be a partial
-- index (WHERE superseded_at IS NULL) -- a full unique index across all
-- rows would block a second insert forever after the first supersede,
-- since the OLD (now-superseded) row still occupies that exact key.
-- Superseded rows are kept for provenance (RFC S5/S12) and must be free
-- to accumulate multiple historical entries for the same triple.
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_enrichment_fields_company_field_source
    ON company_enrichment_fields (company_id, field_name, source)
    WHERE superseded_at IS NULL;

-- Read-model support: "current fields for this company" without a
-- full-table scan or a superseded_at IS NULL filter on every query plan.
CREATE INDEX IF NOT EXISTS ix_company_enrichment_fields_company
    ON company_enrichment_fields (company_id)
    WHERE superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS company_enrichment_jobs (
    id                   SERIAL PRIMARY KEY,
    run_id               VARCHAR(36) NOT NULL,
    company_id           INTEGER NOT NULL REFERENCES companies (id),
    trigger              VARCHAR(20) NOT NULL
        CONSTRAINT ck_company_enrichment_jobs_trigger
        CHECK (trigger IN ('profile_view', 'agent', 'manual')),
    status               VARCHAR(20) NOT NULL DEFAULT 'running'
        CONSTRAINT ck_company_enrichment_jobs_status
        CHECK (status IN ('running', 'success', 'failed', 'partial_success')),
    providers_attempted  VARCHAR(30)[] NOT NULL DEFAULT '{}',
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at          TIMESTAMPTZ,
    lease_expires_at     TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_company_enrichment_jobs_run_id
    ON company_enrichment_jobs (run_id);

-- The in-flight dedup guarantee (RFC S7 step 3, golden case #5): at most
-- one 'running' job per company_id at a time. A concurrent second request
-- for the same company_id must find (and reuse) this row's run_id instead
-- of inserting a second one.
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_enrichment_jobs_company_active
    ON company_enrichment_jobs (company_id)
    WHERE status = 'running';

-- Read-model support: "most recent job(s) for this company" queries.
CREATE INDEX IF NOT EXISTS ix_company_enrichment_jobs_company_started_at
    ON company_enrichment_jobs (company_id, started_at);
