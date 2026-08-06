-- Migration 032: Persistent pipeline coordinator state (R1).
--
-- Additive only. Creates two new tables; touches no existing table.
--
-- Replaces pipeline/run_coordinator.py's local .pipeline/run_coordinator.json
-- + threading.Lock() with durable, transactional PostgreSQL state, so
-- coordinator state survives process restart and is visible across
-- Railway's separate scheduler/subprocess processes (threading.Lock is
-- process-local memory and never shared with the subprocess.Popen-spawned
-- run_pipeline.py child -- see PR-0 architectural inventory).
--
-- pipeline_runs (the existing append-only per-step run-history table used
-- by monitoring) is NOT touched or repurposed by this migration -- it
-- keeps its existing role. This is a narrow, separate backing store for
-- the coordinator's own root/step state only.
--
-- pipeline_coordinator_runs: one row per run_id ("root state"). A partial
-- unique index enforces at most one row with status='active' per
-- pipeline_scope at the database level -- this is what makes "only one
-- active tender-scrape/import run at a time" a real constraint the
-- database enforces, not just an in-process check. lease_expires_at is a
-- heartbeat-renewed TTL: every coordinator state-mutating call extends it,
-- so a run that stops making progress (crashed process, killed container)
-- naturally becomes reclaimable once its lease expires, instead of
-- blocking every future run forever. This is stale-run recovery: it never
-- blocks indefinitely after a restart, and it never silently overwrites
-- another still-live run's state.
--
-- pipeline_coordinator_steps: one row per (run_id, step) -- a unique index
-- on (run_id, step) makes marking the same step twice for the same run an
-- idempotent no-op (via ON CONFLICT DO NOTHING in application code) rather
-- than a race condition, so two concurrent callers marking the same step
-- for the same run can never corrupt the "which steps are done" count.
--
-- NOT wired into db.connection._run_migrations() / init_db() -- unlike
-- most other migrations in this repo, this schema stays inert everywhere
-- (local, staging, production) until an operator explicitly runs
-- scripts/run_pipeline_coordinator_state_migration.py --apply. The tables
-- pipeline/run_coordinator_postgres.py reads/writes (selected only via
-- PIPELINE_COORDINATOR_BACKEND=postgres, default "legacy" -- see
-- pipeline/run_coordinator.py) are declared as plain SQLAlchemy Core
-- Table objects (db/pipeline_coordinator_tables.py), not db.models ORM
-- classes on Base -- Base.metadata.create_all(), which init_db() calls
-- unconditionally on every app startup/deploy, therefore can never
-- auto-create this schema either. Uses the same Class-D dry-run/apply/
-- postcondition-verification CLI pattern as migration 031
-- (db/pipeline_coordinator_migration.py), even though these are two
-- brand-new empty tables rather than an ALTER on a populated one.

CREATE TABLE IF NOT EXISTS pipeline_coordinator_runs (
    id                         SERIAL PRIMARY KEY,
    run_id                     VARCHAR(36) NOT NULL,
    pipeline_scope             VARCHAR(50) NOT NULL DEFAULT 'tender_data',
    status                     VARCHAR(20) NOT NULL DEFAULT 'active',
    phase                      VARCHAR(30) NOT NULL DEFAULT 'idle',
    tender_scrape_started_at   TIMESTAMPTZ,
    tender_scrape_finished_at  TIMESTAMPTZ,
    scrape_phase_started_at    TIMESTAMPTZ,
    scrape_phase_finished_at   TIMESTAMPTZ,
    import_started_at          TIMESTAMPTZ,
    import_finished_at         TIMESTAMPTZ,
    finished_at                TIMESTAMPTZ,
    success                    BOOLEAN,
    error                      TEXT NOT NULL DEFAULT '',
    stale_reclaimed            BOOLEAN NOT NULL DEFAULT FALSE,
    lease_expires_at           TIMESTAMPTZ NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_coordinator_runs_run_id
    ON pipeline_coordinator_runs (run_id);

-- The database-level guarantee: at most one 'active' row per scope, ever.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_coordinator_runs_active_scope
    ON pipeline_coordinator_runs (pipeline_scope)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_pipeline_coordinator_runs_scope_status
    ON pipeline_coordinator_runs (pipeline_scope, status);

CREATE TABLE IF NOT EXISTS pipeline_coordinator_steps (
    id           SERIAL PRIMARY KEY,
    run_id       VARCHAR(36) NOT NULL REFERENCES pipeline_coordinator_runs (run_id),
    step         VARCHAR(100) NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_coordinator_steps_run_step
    ON pipeline_coordinator_steps (run_id, step);

CREATE INDEX IF NOT EXISTS ix_pipeline_coordinator_steps_run_id
    ON pipeline_coordinator_steps (run_id);
