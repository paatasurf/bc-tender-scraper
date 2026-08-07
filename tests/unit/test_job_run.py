"""Tests for pipeline/job_run.py -- the M3B writer API for ops_job_runs /
ops_job_run_events. Pure-logic tests (counts validation, trigger/status/
event_type validation) need no database. Lifecycle tests use a real local
Postgres with migration 033 applied directly (same fixture convention as
tests/unit/test_pipeline_coordinator_db.py's coordinator_db).
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from db.ops_job_run_ddl import ops_job_run_migration_statements
from db.ops_job_run_tables import ops_job_run_events, ops_job_runs
from pipeline.error_classification import ERROR_SUMMARY_LABELS
from pipeline.job_run import (
    InvalidCountsError,
    finish_job_run,
    heartbeat_job_run,
    record_job_step,
    start_job_run,
    validate_counts,
)
from tests.db_test_safety import require_local_test_database

# ---------------------------------------------------------------------
# Pure: validate_counts
# ---------------------------------------------------------------------


def test_validate_counts_accepts_int_float_bool_null_values():
    payload = {"found": 5, "ratio": 0.5, "ok": True, "skipped": None}
    assert validate_counts(payload) == payload


def test_validate_counts_accepts_empty_dict():
    assert validate_counts({}) == {}


def test_validate_counts_rejects_non_dict():
    for bad in ("a string", [1, 2, 3], 42, None):
        with pytest.raises(InvalidCountsError):
            validate_counts(bad)


def test_validate_counts_rejects_string_values():
    with pytest.raises(InvalidCountsError):
        validate_counts({"error": "some free-text stack trace or secret"})


def test_validate_counts_rejects_nested_dict_values():
    with pytest.raises(InvalidCountsError):
        validate_counts({"nested": {"found": 1}})


def test_validate_counts_rejects_list_values():
    with pytest.raises(InvalidCountsError):
        validate_counts({"items": [1, 2, 3]})


def test_validate_counts_error_message_never_echoes_the_rejected_value():
    secret_like = "postgresql://user:hunter2@host/db"
    try:
        validate_counts({"raw": secret_like})
        pytest.fail("expected InvalidCountsError")
    except InvalidCountsError as exc:
        assert secret_like not in str(exc)
        assert "hunter2" not in str(exc)


def test_validate_counts_error_message_never_echoes_a_secret_like_key_either():
    """The key itself, not just the value, could carry a secret/PII
    fragment (e.g. a caller accidentally using a token or an email
    address as a dict key) -- the exception message must contain neither
    the key nor the value, only safe type-name text."""
    secret_key = "api_key=sk_live_abcdef123456"
    secret_value = "Authorization: Bearer hunter2-token"
    try:
        validate_counts({secret_key: secret_value})
        pytest.fail("expected InvalidCountsError")
    except InvalidCountsError as exc:
        message = str(exc)
        assert secret_key not in message
        assert secret_value not in message
        assert "sk_live" not in message
        assert "hunter2" not in message
        assert message == "counts values must be numbers, booleans, or null; got str"


# ---------------------------------------------------------------------
# Pure: trigger/status/event_type validation (no DB needed to reach the
# ValueError -- raised before any query)
# ---------------------------------------------------------------------


def test_start_job_run_rejects_invalid_trigger():
    with pytest.raises(ValueError, match="trigger"):
        start_job_run(
            session=None,  # never reached -- validation happens first
            job_type="ai_scoring",
            trigger="cron",  # not one of scheduler|manual|n8n
        )


def test_finish_job_run_rejects_non_terminal_status():
    with pytest.raises(ValueError, match="status"):
        finish_job_run(session=None, run_id="whatever", status="running")


def test_finish_job_run_rejects_stale_as_a_status():
    """'stale' must never be an accepted status value -- it is a read-model
    interpretation (running + expired lease), never something this writer
    can be asked to set directly."""
    with pytest.raises(ValueError, match="status"):
        finish_job_run(session=None, run_id="whatever", status="stale")


def test_record_job_step_rejects_invalid_event_type():
    with pytest.raises(ValueError, match="event_type"):
        record_job_step(session=None, run_id="whatever", event_type="heartbeat")


def test_record_job_step_rejects_started_reserved_for_start_job_run():
    with pytest.raises(ValueError, match="event_type"):
        record_job_step(session=None, run_id="whatever", event_type="started")


def test_record_job_step_rejects_finished_reserved_for_finish_job_run():
    with pytest.raises(ValueError, match="event_type"):
        record_job_step(session=None, run_id="whatever", event_type="finished")


# ---------------------------------------------------------------------
# Real local-Postgres: lifecycle
# ---------------------------------------------------------------------


@pytest.fixture
def job_run_db():
    """Real local-Postgres-backed ops_job_run schema, reset before and
    after each test."""
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


def _row(engine, run_id: str) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(select(ops_job_runs).where(ops_job_runs.c.run_id == run_id))
            .mappings()
            .first()
        )
    assert row is not None
    return dict(row)


def _events(engine, run_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(ops_job_run_events)
                .where(ops_job_run_events.c.run_id == run_id)
                .order_by(
                    ops_job_run_events.c.occurred_at.asc(),
                    ops_job_run_events.c.id.asc(),
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _session_for(engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine)()


def test_lifecycle_start_heartbeat_step_events_success(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(
            session,
            job_type="surrey_identity_scheduler",
            trigger="scheduler",
            source="surrey",
            idempotency_key="day-2026-08-06",
            counts={"source_rows": 0},
        )

        row = _row(engine, run_id)
        assert row["status"] == "running"
        assert row["trigger"] == "scheduler"
        assert row["job_type"] == "surrey_identity_scheduler"
        assert row["started_at"] is not None
        assert row["heartbeat_at"] is not None
        assert row["finished_at"] is None
        assert row["lease_expires_at"] is not None
        assert row["error_present"] is False

        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == ["started"]

        heartbeat_job_run(session, run_id, lease_ttl=timedelta(minutes=45))
        row_after_heartbeat = _row(engine, run_id)
        assert row_after_heartbeat["heartbeat_at"] >= row["heartbeat_at"]
        assert row_after_heartbeat["lease_expires_at"] > row["lease_expires_at"]
        # Heartbeat must NOT create an event row.
        assert len(_events(engine, run_id)) == 1

        record_job_step(session, run_id, event_type="step_started", step="plan")
        record_job_step(
            session,
            run_id,
            event_type="step_completed",
            step="plan",
            counts_delta={"planned_updates": 3},
        )
        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == [
            "started",
            "step_started",
            "step_completed",
        ]
        assert events[-1]["counts_delta"] == {"planned_updates": 3}

        finish_job_run(
            session, run_id, status="success", counts={"source_rows": 12, "updated": 3}
        )
        final_row = _row(engine, run_id)
        assert final_row["status"] == "success"
        assert final_row["finished_at"] is not None
        assert final_row["counts"] == {"source_rows": 12, "updated": 3}
        assert final_row["error_present"] is False
        assert final_row["error_summary"] is None

        events = _events(engine, run_id)
        assert events[-1]["event_type"] == "finished"
    finally:
        session.close()


def test_finish_job_run_failed_classifies_raw_error_safely(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="scheduler")
        raw_error = "Failed to connect: postgresql://scraper_user:hunter2@10.0.0.5:5432/production"
        finish_job_run(session, run_id, status="failed", raw_error=raw_error)

        row = _row(engine, run_id)
        assert row["status"] == "failed"
        assert row["error_present"] is True
        assert row["error_summary"] in ERROR_SUMMARY_LABELS

        # The raw error text must never appear anywhere in the persisted
        # row, under any column.
        serialized = json.dumps({k: v for k, v in row.items()}, default=str)
        assert "hunter2" not in serialized
        assert "postgresql://" not in serialized
    finally:
        session.close()


def test_finish_job_run_partial_failure(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(
            session, job_type="arch_company_intelligence", trigger="scheduler"
        )
        finish_job_run(
            session,
            run_id,
            status="partial_failure",
            counts={"populated": 10, "houzz_scraped": 0},
            raw_error="Houzz scrape failed: HTTP 503 Service Unavailable",
        )
        row = _row(engine, run_id)
        assert row["status"] == "partial_failure"
        assert row["error_present"] is True
        assert row["error_summary"] == "http_5xx"
        assert row["counts"] == {"populated": 10, "houzz_scraped": 0}
    finally:
        session.close()


def test_heartbeat_is_a_silent_noop_on_a_terminal_run(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="manual")
        finish_job_run(session, run_id, status="success")
        finished_row = _row(engine, run_id)

        heartbeat_job_run(session, run_id)  # must not raise, must not resurrect

        row_after = _row(engine, run_id)
        assert row_after["status"] == "success"
        assert row_after["heartbeat_at"] == finished_row["heartbeat_at"]
        assert row_after["lease_expires_at"] == finished_row["lease_expires_at"]
    finally:
        session.close()


def test_finish_job_run_is_a_silent_noop_when_already_terminal(job_run_db):
    """A second finish_job_run() call on an already-terminal run must not
    silently overwrite the first terminal outcome."""
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="manual")
        finish_job_run(session, run_id, status="success", counts={"scored": 5})
        first = _row(engine, run_id)

        finish_job_run(session, run_id, status="failed", raw_error="should not apply")
        second = _row(engine, run_id)

        assert second["status"] == first["status"] == "success"
        assert second["counts"] == {"scored": 5}
        assert second["error_present"] is False
    finally:
        session.close()


def test_stale_is_never_written_as_a_status_only_a_read_interpretation(job_run_db):
    """Core M3B semantic: nothing in pipeline/job_run.py ever writes
    status='stale'. A run whose lease has expired stays status='running'
    in the table -- "stale" only exists as a future read-model
    interpretation (status='running' AND lease_expires_at <= now())."""
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(
            session,
            job_type="ai_scoring",
            trigger="scheduler",
            lease_ttl=timedelta(seconds=-1),
        )
        row = _row(engine, run_id)
        # Lease is already expired (negative TTL), but the stored status
        # is untouched by anything in this module.
        assert row["status"] == "running"
        assert row["lease_expires_at"] < row["started_at"]

        with engine.connect() as conn:
            valid_statuses = (
                conn.execute(text("SELECT DISTINCT status FROM ops_job_runs"))
                .scalars()
                .all()
            )
        assert "stale" not in valid_statuses
    finally:
        session.close()


def test_start_job_run_idempotency_key_conflict_raises_integrity_error(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        start_job_run(
            session,
            job_type="surrey_identity_scheduler",
            trigger="scheduler",
            idempotency_key="day-2026-08-06",
        )
        with pytest.raises(IntegrityError):
            start_job_run(
                session,
                job_type="surrey_identity_scheduler",
                trigger="scheduler",
                idempotency_key="day-2026-08-06",
            )
    finally:
        session.rollback()
        session.close()


def test_record_job_step_validates_counts_delta(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(
            session, job_type="company_intelligence", trigger="scheduler"
        )
        with pytest.raises(InvalidCountsError):
            record_job_step(
                session,
                run_id,
                event_type="step_failed",
                step="google_enrich",
                counts_delta={"error": "some raw text"},
            )
    finally:
        session.close()


# ---------------------------------------------------------------------
# Review-fix regression: expired-lease / idempotent-terminal-transition
# semantics for heartbeat_job_run / record_job_step / finish_job_run
# ---------------------------------------------------------------------


def test_heartbeat_does_not_extend_an_already_expired_lease(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(
            session,
            job_type="ai_scoring",
            trigger="scheduler",
            lease_ttl=timedelta(seconds=-1),  # already expired at creation
        )
        before = _row(engine, run_id)
        assert before["status"] == "running"
        assert before["lease_expires_at"] < before["started_at"]

        heartbeat_job_run(session, run_id, lease_ttl=timedelta(minutes=45))

        after = _row(engine, run_id)
        # No-op: heartbeat_at/lease_expires_at must be UNCHANGED.
        assert after["heartbeat_at"] == before["heartbeat_at"]
        assert after["lease_expires_at"] == before["lease_expires_at"]
        assert after["status"] == "running"  # still not silently transitioned
    finally:
        session.close()


def test_repeat_finish_does_not_add_a_second_finished_event(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="manual")
        finish_job_run(session, run_id, status="success")
        finish_job_run(session, run_id, status="success")
        finish_job_run(session, run_id, status="failed", raw_error="ignored")

        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == ["started", "finished"]
        assert _row(engine, run_id)["status"] == "success"
    finally:
        session.close()


def test_finish_of_unknown_run_id_is_a_noop_with_no_event(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        finish_job_run(
            session, "no-such-run-id-ever", status="success"
        )  # must not raise
        events = _events(engine, "no-such-run-id-ever")
        assert events == []
    finally:
        session.close()


def test_step_event_not_added_after_terminal_run(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="scheduler")
        finish_job_run(session, run_id, status="success")

        record_job_step(session, run_id, event_type="step_completed", step="too-late")

        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == ["started", "finished"]
    finally:
        session.close()


def test_step_event_not_added_after_lease_expired_even_if_status_still_running(
    job_run_db,
):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(
            session,
            job_type="ai_scoring",
            trigger="scheduler",
            lease_ttl=timedelta(seconds=-1),
        )
        assert _row(engine, run_id)["status"] == "running"  # stale, not terminal

        record_job_step(session, run_id, event_type="step_started", step="too-late")

        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == ["started"]
    finally:
        session.close()


def test_step_event_not_added_for_unknown_run_id(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        record_job_step(session, "no-such-run-id-ever", event_type="step_started")
        events = _events(engine, "no-such-run-id-ever")
        assert events == []
    finally:
        session.close()


def test_normal_active_run_still_records_step_and_finish_events(job_run_db):
    """The fixes must not make a genuinely active run silently drop its
    own events -- only stale/terminal/nonexistent runs are affected."""
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="scheduler")
        record_job_step(session, run_id, event_type="step_started", step="score")
        record_job_step(
            session,
            run_id,
            event_type="step_completed",
            step="score",
            counts_delta={"scored": 5},
        )
        finish_job_run(session, run_id, status="success", counts={"scored": 5})

        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == [
            "started",
            "step_started",
            "step_completed",
            "finished",
        ]
        assert _row(engine, run_id)["status"] == "success"
    finally:
        session.close()


def test_concurrency_finish_and_step_cannot_both_add_event_after_terminal_transition(
    job_run_db,
):
    """Real concurrent race: one thread calls finish_job_run(), another
    calls record_job_step() for the same run_id at the same time.
    Whichever transaction's row-lock/UPDATE commits first must determine
    what the other sees -- the step call must never manage to insert an
    event once the run is (or is concurrently becoming) terminal."""
    import threading

    engine = job_run_db
    setup_session = _session_for(engine)
    try:
        run_id = start_job_run(
            setup_session, job_type="ai_scoring", trigger="scheduler"
        )
    finally:
        setup_session.close()

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _finish() -> None:
        barrier.wait(timeout=5)
        session = _session_for(engine)
        try:
            finish_job_run(session, run_id, status="success")
        except Exception as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)
        finally:
            session.close()

    def _step() -> None:
        barrier.wait(timeout=5)
        session = _session_for(engine)
        try:
            record_job_step(session, run_id, event_type="step_completed", step="race")
        except Exception as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=_finish), threading.Thread(target=_step)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == [], errors

    events = _events(engine, run_id)
    event_types = [e["event_type"] for e in events]
    assert event_types.count("finished") == 1
    if "step_completed" in event_types:
        # The step event, if it landed at all, must have landed BEFORE
        # the terminal transition -- never after.
        assert event_types.index("step_completed") < event_types.index("finished")
    assert _row(engine, run_id)["status"] == "success"


# ---------------------------------------------------------------------
# Review-fix regression: record_job_step() reserves started/finished for
# start_job_run()/finish_job_run()
# ---------------------------------------------------------------------


def test_record_job_step_started_is_rejected_and_creates_no_event(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="scheduler")
        before = _events(engine, run_id)

        with pytest.raises(ValueError, match="event_type"):
            record_job_step(session, run_id, event_type="started")

        after = _events(engine, run_id)
        assert after == before
        assert [e["event_type"] for e in after] == ["started"]
    finally:
        session.close()


def test_record_job_step_finished_is_rejected_and_creates_no_event(job_run_db):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="scheduler")
        before = _events(engine, run_id)

        with pytest.raises(ValueError, match="event_type"):
            record_job_step(session, run_id, event_type="finished")

        after = _events(engine, run_id)
        assert after == before
        assert [e["event_type"] for e in after] == ["started"]
        assert _row(engine, run_id)["status"] == "running"  # not accidentally finished
    finally:
        session.close()


@pytest.mark.parametrize(
    "event_type", ["step_started", "step_completed", "step_failed"]
)
def test_record_job_step_allows_the_three_step_event_types(job_run_db, event_type):
    engine = job_run_db
    session = _session_for(engine)
    try:
        run_id = start_job_run(session, job_type="ai_scoring", trigger="scheduler")
        record_job_step(session, run_id, event_type=event_type, step="some-step")

        events = _events(engine, run_id)
        assert [e["event_type"] for e in events] == ["started", event_type]
    finally:
        session.close()
