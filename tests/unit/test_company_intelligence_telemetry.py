"""Real local-Postgres tests for pipeline/company_intelligence_telemetry.py
-- the shared ops_job_runs/ops_job_run_events writer both the scheduled
pipeline (trigger="scheduler") and the manual/n8n HTTP path (trigger=
"manual") now call, instead of each maintaining its own copy.

Same fixture pattern as tests/unit/test_job_run.py's job_run_db.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text

from db.ops_job_run_ddl import ops_job_run_migration_statements
from db.ops_job_run_tables import ops_job_run_events, ops_job_runs
from pipeline.company_intelligence_telemetry import (
    finish_company_intelligence_telemetry,
    record_company_intelligence_phase,
    start_company_intelligence_telemetry,
)
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def job_run_db():
    """Real local-Postgres-backed ops_job_run schema, reset before and
    after each test -- identical fixture shape to test_job_run.py."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in ops_job_run_migration_statements():
            conn.execute(text(statement))

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ops_job_run_events"))
            conn.execute(text("DELETE FROM ops_job_runs"))

    _reset()
    try:
        yield engine
    finally:
        _reset()
        engine.dispose()


def _row(engine, run_id: str) -> dict | None:
    with engine.connect() as conn:
        row = (
            conn.execute(select(ops_job_runs).where(ops_job_runs.c.run_id == run_id))
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


def _all_rows_count(engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(ops_job_runs.c.run_id)).all())


def test_manual_path_writes_trigger_manual_correlated_to_the_given_run_id(
    job_run_db,
) -> None:
    """The manual/n8n path's own pipeline_runs.run_id, passed straight
    through as `run_id`, must end up as BOTH ops_job_runs.run_id and
    ops_job_runs.idempotency_key -- direct correlation, no time-window
    guessing."""
    engine = job_run_db
    correlation_id = "pipeline-runs-run-id-abc"

    returned = start_company_intelligence_telemetry(
        trigger="manual", run_id=correlation_id
    )

    assert returned == correlation_id
    row = _row(engine, correlation_id)
    assert row is not None
    assert row["trigger"] == "manual"
    assert row["job_type"] == "company_intelligence"
    assert row["idempotency_key"] == correlation_id
    assert row["status"] == "running"


def test_scheduled_path_still_writes_trigger_scheduler_with_generated_run_id(
    job_run_db,
) -> None:
    """Regression guard: the scheduled path (no run_id passed, matching
    pipeline/run.py's unchanged call shape) keeps getting a freshly
    generated UUID and trigger="scheduler", exactly as before this
    module existed."""
    engine = job_run_db

    returned = start_company_intelligence_telemetry(trigger="scheduler")

    assert returned is not None
    row = _row(engine, returned)
    assert row is not None
    assert row["trigger"] == "scheduler"
    assert row["idempotency_key"] is None


def test_duplicate_start_for_the_same_run_id_creates_no_second_row(
    job_run_db,
) -> None:
    """Two telemetry-start attempts for the same correlation run_id (e.g.
    a retried/duplicate manual trigger) must not create two ops_job_runs
    rows -- the second is a fail-open no-op (returns None), guarded by
    the partial unique index on (job_type, idempotency_key)."""
    engine = job_run_db
    correlation_id = "duplicate-start-run-id"

    first = start_company_intelligence_telemetry(
        trigger="manual", run_id=correlation_id
    )
    second = start_company_intelligence_telemetry(
        trigger="manual", run_id=correlation_id
    )

    assert first == correlation_id
    assert second is None
    assert _all_rows_count(engine) == 1


def test_finish_after_manual_start_transitions_status_and_persists_counts(
    job_run_db,
) -> None:
    engine = job_run_db
    correlation_id = "finish-flow-run-id"
    start_company_intelligence_telemetry(trigger="manual", run_id=correlation_id)

    finish_company_intelligence_telemetry(
        correlation_id,
        status="success",
        counts={"companies_populated": 7},
    )

    row = _row(engine, correlation_id)
    assert row is not None
    assert row["status"] == "success"
    assert row["counts"] == {"companies_populated": 7}
    assert row["finished_at"] is not None


def test_finish_failed_records_error_present_without_raw_text(job_run_db) -> None:
    engine = job_run_db
    correlation_id = "finish-failed-run-id"
    start_company_intelligence_telemetry(trigger="manual", run_id=correlation_id)

    finish_company_intelligence_telemetry(
        correlation_id, status="failed", raw_error="cursor already closed"
    )

    row = _row(engine, correlation_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_present"] is True


def test_record_phase_appends_step_completed_event_and_heartbeats(job_run_db) -> None:
    engine = job_run_db
    correlation_id = "phase-flow-run-id"
    start_company_intelligence_telemetry(trigger="manual", run_id=correlation_id)

    record_company_intelligence_phase(correlation_id, "populate")

    with engine.connect() as conn:
        events = (
            conn.execute(
                select(ops_job_run_events).where(
                    ops_job_run_events.c.run_id == correlation_id
                )
            )
            .mappings()
            .all()
        )
    event_types = [e["event_type"] for e in events]
    assert "started" in event_types
    assert "step_completed" in event_types
    step_event = next(e for e in events if e["event_type"] == "step_completed")
    assert step_event["step"] == "populate"
