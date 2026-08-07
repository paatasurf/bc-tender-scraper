-- Migration 033: Ops job run / job run event schema foundation (M3B).
--
-- Additive only. Creates two new tables; touches no existing table.
--
-- Inert schema foundation for future persisted job history/heartbeats for
-- job types that today write NOTHING queryable anywhere: the Surrey
-- identity scheduler (pipeline/surrey_identity_scheduler.py -- its result
-- is only ever logger.info()'d), AI scoring, company intelligence, and
-- arch-company-intelligence (all three called from pipeline/run.py's
-- run_pipeline(), which only print()s). See the M3A architecture audit
-- for the full research this schema is based on.
--
-- Deliberately NOT the same table as pipeline_runs (the existing
-- append-only, no-TTL, per-step table used by ~15 internal/admin/n8n
-- endpoints) or pipeline_coordinator_runs/steps (the tender_data-scope-
-- only coordinator lease from migration 032). ops_job_runs is a new,
-- general-purpose model for job types outside both of those -- neither
-- existing table is touched, repurposed, or migrated by this change.
--
-- ops_job_runs: one row per run_id. lease_expires_at is a heartbeat-
-- renewed TTL, the same pattern migration 032 already proved in
-- production for pipeline_coordinator_runs -- a run that stops making
-- progress (crashed process, Railway restart on failure -- see
-- railway.toml's restartPolicyMaxRetries=10, a real, recurring event, not
-- a hypothetical) naturally becomes reclaimable once its lease expires,
-- rather than being trusted as "still running" forever. "stale" is
-- deliberately NOT a status value stored here -- see
-- pipeline/job_run.py's module docstring: it is a read-model
-- interpretation (status='running' AND lease_expires_at <= now()), never
-- a value this schema or its writer ever assigns.
--
-- ops_job_run_events: append-only log of milestone events within one run
-- (started / step_started / step_completed / step_failed / finished).
-- Deliberately NOT one row per heartbeat -- heartbeats only touch
-- ops_job_runs.heartbeat_at/lease_expires_at in place; see
-- pipeline/job_run.py::heartbeat_job_run().
--
-- NOT wired into db.connection._run_migrations() / init_db() -- this
-- schema stays inert everywhere (local, staging, production) until an
-- operator explicitly runs scripts/run_ops_job_run_migration.py --apply.
-- The tables pipeline/job_run.py reads/writes are declared as plain
-- SQLAlchemy Core Table objects (db/ops_job_run_tables.py), not
-- db.models ORM classes on Base -- Base.metadata.create_all(), which
-- init_db() calls unconditionally on every app startup/deploy, therefore
-- can never auto-create this schema either. Uses the same Class-D
-- dry-run/apply/postcondition-verification CLI pattern as migration 032
-- (db/ops_job_run_migration.py).
--
-- M3B ships this schema and pipeline/job_run.py's writer API only -- no
-- existing job (Surrey scheduler, AI scoring, company intelligence,
-- arch-company-intelligence, scheduler.py, any api/internal.py endpoint)
-- is wired to call it in this migration/PR. See M3C/M3D in the M3A audit
-- for actual instrumentation.
--
-- CHECK constraints on status/trigger/event_type are a database-level
-- backstop -- pipeline/job_run.py already validates all three in Python
-- before ever writing, but these constraints mean a direct/manual SQL
-- INSERT or UPDATE (bypassing that module entirely) can never leave an
-- invalid value in any of these columns either. Named explicitly so
-- db/ops_job_run_migration.py's schema-contract check can verify their
-- presence by name.

CREATE TABLE IF NOT EXISTS ops_job_runs (
    id                SERIAL PRIMARY KEY,
    run_id            VARCHAR(36) NOT NULL,
    job_type          VARCHAR(50) NOT NULL,
    source            VARCHAR(50),
    trigger           VARCHAR(20) NOT NULL
        CONSTRAINT ck_ops_job_runs_trigger CHECK (trigger IN ('scheduler', 'manual', 'n8n')),
    status            VARCHAR(20) NOT NULL DEFAULT 'running'
        CONSTRAINT ck_ops_job_runs_status
        CHECK (status IN ('running', 'success', 'failed', 'partial_failure')),
    started_at        TIMESTAMPTZ,
    heartbeat_at      TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    lease_expires_at  TIMESTAMPTZ NOT NULL,
    counts            JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_present     BOOLEAN NOT NULL DEFAULT FALSE,
    error_summary     VARCHAR(20),
    idempotency_key   VARCHAR(128),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_ops_job_runs_run_id
    ON ops_job_runs (run_id);

-- Idempotency: at most one row per (job_type, idempotency_key) when a
-- caller supplies one at all. Callers that pass no idempotency_key
-- (idempotency_key IS NULL) are entirely unconstrained by this index --
-- e.g. ad-hoc manual runs where a repeat is expected and fine.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ops_job_runs_job_type_idempotency_key
    ON ops_job_runs (job_type, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Read-model support: "most recent run(s) per job_type" / "all currently
-- running rows of this job_type" queries.
CREATE INDEX IF NOT EXISTS ix_ops_job_runs_job_type_status
    ON ops_job_runs (job_type, status);

-- Read-model support: cheaply finding running rows whose lease has (or
-- hasn't) expired, without a full-table scan.
CREATE INDEX IF NOT EXISTS ix_ops_job_runs_status_lease_expires_at
    ON ops_job_runs (status, lease_expires_at);

CREATE TABLE IF NOT EXISTS ops_job_run_events (
    id            SERIAL PRIMARY KEY,
    run_id        VARCHAR(36) NOT NULL REFERENCES ops_job_runs (run_id),
    event_type    VARCHAR(20) NOT NULL
        CONSTRAINT ck_ops_job_run_events_event_type
        CHECK (event_type IN ('started', 'step_started', 'step_completed', 'step_failed', 'finished')),
    step          VARCHAR(100),
    counts_delta  JSONB,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Covers both "all events for this run_id" and "...in chronological
-- order" from a single composite index (leftmost-prefix run_id lookups
-- are covered by the same index, so no separate single-column index is
-- needed here).
CREATE INDEX IF NOT EXISTS ix_ops_job_run_events_run_id_occurred_at
    ON ops_job_run_events (run_id, occurred_at);
