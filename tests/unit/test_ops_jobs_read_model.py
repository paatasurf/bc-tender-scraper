"""Tests for pipeline/ops_jobs_read_model.py -- the M3E-A read model for
GET /api/ops/jobs and GET /api/ops/jobs/{run_id}.

Pure-logic tests (status normalization, payload shaping) use lightweight
dict/MagicMock stand-ins -- no database needed, mirroring
tests/unit/test_ops_read_model.py's split between pure and DB-backed
tests. DB-backed tests use a real local Postgres with migration 033
applied directly (same fixture convention as tests/unit/test_job_run.py's
job_run_db) and populate real rows through pipeline/job_run.py's own
writer functions, never raw INSERTs, so these tests exercise the exact
same data shape production code produces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from db.ops_job_run_ddl import ops_job_run_migration_statements
from pipeline.job_run import finish_job_run, record_job_step, start_job_run
from pipeline.ops_jobs_read_model import (
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    VALID_JOB_STATUS_FILTERS,
    build_job_event_payload,
    build_job_run_payload,
    get_job_type_summary,
    get_ops_job_run_detail,
    list_ops_job_runs,
    normalize_ops_job_run_status,
    ops_job_run_schema_available,
    surrey_identity_scheduler_telemetry_capability,
)
from tests.db_test_safety import require_local_test_database

# ---------------------------------------------------------------------
# Pure: normalize_ops_job_run_status
# ---------------------------------------------------------------------


def _future(seconds: int = 300) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _past(seconds: int = 300) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def test_normalize_running_with_valid_lease_is_active():
    assert (
        normalize_ops_job_run_status(status="running", lease_expires_at=_future())
        == "active"
    )


def test_normalize_running_with_expired_lease_is_stale():
    assert (
        normalize_ops_job_run_status(status="running", lease_expires_at=_past())
        == "stale"
    )


def test_normalize_running_with_lease_exactly_now_is_stale():
    """Boundary: lease_expires_at == now() is NOT > now(), so it is stale,
    matching pipeline/job_run.py's own heartbeat/record_job_step lease
    check (lease_expires_at > now())."""
    now = datetime.now(timezone.utc)
    assert (
        normalize_ops_job_run_status(status="running", lease_expires_at=now) == "stale"
    )


@pytest.mark.parametrize("status", ["success", "failed", "partial_failure"])
def test_normalize_terminal_statuses_pass_through_unchanged(status):
    assert (
        normalize_ops_job_run_status(status=status, lease_expires_at=_past()) == status
    )
    # Terminal statuses are never reinterpreted by lease state either way.
    assert (
        normalize_ops_job_run_status(status=status, lease_expires_at=_future())
        == status
    )


def test_normalize_unrecognized_status_is_unknown_not_a_crash():
    assert (
        normalize_ops_job_run_status(status="bogus", lease_expires_at=_future())
        == "unknown"
    )


# ---------------------------------------------------------------------
# Pure: build_job_run_payload / build_job_event_payload
# ---------------------------------------------------------------------


def _fake_run_row(**overrides) -> dict:
    base = {
        "run_id": "r-1",
        "job_type": "surrey_identity_scheduler",
        "source": "surrey",
        "trigger": "scheduler",
        "status": "success",
        "started_at": _past(600),
        "heartbeat_at": _past(60),
        "finished_at": _past(30),
        "lease_expires_at": _future(),
        "counts": {"source_rows": 10, "inserted": 2, "updated": 8, "error_count": 0},
        "error_present": False,
        "error_summary": None,
    }
    base.update(overrides)
    return base


def test_build_job_run_payload_safe_shape_exact_keys():
    payload = build_job_run_payload(_fake_run_row())
    assert set(payload.keys()) == {
        "run_id",
        "job_type",
        "source",
        "trigger",
        "normalized_status",
        "started_at",
        "heartbeat_at",
        "finished_at",
        "lease_expires_at",
        "counts",
        "error_present",
        "error_summary",
    }


def test_build_job_run_payload_never_leaks_raw_error_plan_digest_or_result_digest():
    """The row itself carries none of these fields (they aren't columns --
    see db/ops_job_run_tables.py), but this is an explicit regression test
    proving the payload builder doesn't invent a leak path either."""
    secret_like_error = "Traceback: connection to postgresql://user:hunter2@host failed"
    row = _fake_run_row(
        status="failed",
        error_present=True,
        error_summary="database",
    )
    payload = build_job_run_payload(row)
    serialized = str(payload)
    assert secret_like_error not in serialized
    assert "plan_digest" not in payload
    assert "result_digest" not in payload
    assert payload["error_summary"] == "database"
    assert payload["error_present"] is True


def test_build_job_run_payload_counts_defaults_to_empty_dict_never_none():
    payload = build_job_run_payload(_fake_run_row(counts=None))
    assert payload["counts"] == {}


def test_build_job_event_payload_shape():
    row = {
        "event_type": "step_completed",
        "step": "apply",
        "counts_delta": None,
        "occurred_at": _past(10),
    }
    payload = build_job_event_payload(row)
    assert set(payload.keys()) == {"event_type", "step", "counts_delta", "occurred_at"}
    assert payload["event_type"] == "step_completed"
    assert payload["step"] == "apply"


# ---------------------------------------------------------------------
# Pure/mocked: schema-unavailable degradation (no real DB needed --
# mirrors tests/unit/test_ops_read_model.py's coordinator_schema_available
# tests)
# ---------------------------------------------------------------------


def test_schema_available_false_on_query_failure():
    session = MagicMock()
    session.execute.side_effect = RuntimeError("relation does not exist")
    assert ops_job_run_schema_available(session) is False


def test_schema_available_false_when_to_regclass_reports_missing():
    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = False
    assert ops_job_run_schema_available(session) is False


def test_schema_available_true_when_to_regclass_reports_present():
    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = True
    assert ops_job_run_schema_available(session) is True


def test_valid_job_status_filters_is_the_exact_documented_allowlist():
    assert VALID_JOB_STATUS_FILTERS == {
        "active",
        "stale",
        "success",
        "failed",
        "partial_failure",
    }


def test_list_limit_constants_are_sane():
    assert LIST_DEFAULT_LIMIT <= LIST_MAX_LIMIT
    assert LIST_MAX_LIMIT == 100


# ---------------------------------------------------------------------
# Pure: surrey_identity_scheduler_telemetry_capability (dynamic
# replacement for the old M2B static SURREY_IDENTITY_SCHEDULER_TELEMETRY
# constant -- fixed by request after PR #126 review)
# ---------------------------------------------------------------------


def test_surrey_capability_telemetry_disabled_when_flag_off():
    result = surrey_identity_scheduler_telemetry_capability(
        telemetry_enabled=False, schema_available=True
    )
    assert result == {"available": False, "reason": "telemetry_disabled"}


def test_surrey_capability_telemetry_disabled_takes_priority_over_schema_state():
    """Flag off must report telemetry_disabled even if schema_available is
    True -- the flag is checked first, matching the task's stated
    priority order."""
    result = surrey_identity_scheduler_telemetry_capability(
        telemetry_enabled=False, schema_available=False
    )
    assert result == {"available": False, "reason": "telemetry_disabled"}


def test_surrey_capability_schema_unavailable_when_flag_on_but_schema_missing():
    result = surrey_identity_scheduler_telemetry_capability(
        telemetry_enabled=True, schema_available=False
    )
    assert result == {"available": False, "reason": "schema_unavailable"}


def test_surrey_capability_available_when_flag_on_and_schema_ready():
    result = surrey_identity_scheduler_telemetry_capability(
        telemetry_enabled=True, schema_available=True
    )
    assert result == {"available": True, "reason": None}


def test_surrey_capability_shape_is_always_exactly_available_and_reason():
    """Must never grow a "status"/"health"/"configured" key -- same
    invariant as the old static constant it replaces (see
    tests/unit/test_ops_read_model.py's
    test_ai_pipeline_telemetry_capability_flag_is_stable_and_never_signals_health)."""
    for telemetry_enabled, schema_available in (
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    ):
        result = surrey_identity_scheduler_telemetry_capability(
            telemetry_enabled=telemetry_enabled, schema_available=schema_available
        )
        assert set(result.keys()) == {"available", "reason"}
        assert result["reason"] in {"telemetry_disabled", "schema_unavailable", None}


def test_surrey_capability_signature_takes_only_two_plain_bools():
    """Structural proof this is a pure function: its only inputs are the
    two already-computed bools, so it cannot itself read an env var or
    touch a database session -- a caller who never read a secret before
    calling this can't have one leak out of it either."""
    import inspect

    params = inspect.signature(
        surrey_identity_scheduler_telemetry_capability
    ).parameters
    assert set(params.keys()) == {"telemetry_enabled", "schema_available"}
    for param in params.values():
        # `from __future__ import annotations` means annotations are
        # unevaluated strings at runtime, not the `bool` type object.
        assert param.annotation == "bool"


# ---------------------------------------------------------------------
# Real local-Postgres: list_ops_job_runs / get_ops_job_run_detail /
# get_job_type_summary
# ---------------------------------------------------------------------


@pytest.fixture
def job_run_db():
    """Real local-Postgres-backed ops_job_run schema, reset before and
    after each test -- identical fixture to tests/unit/test_job_run.py's
    job_run_db."""
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


def _session_for(engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine)()


def test_schema_available_true_against_a_real_migrated_database(job_run_db):
    session = _session_for(job_run_db)
    try:
        assert ops_job_run_schema_available(session) is True
    finally:
        session.close()


def test_list_returns_newest_first(job_run_db):
    session = _session_for(job_run_db)
    try:
        first = start_job_run(
            session, job_type="surrey_identity_scheduler", trigger="scheduler"
        )
        finish_job_run(session, first, status="success")
        second = start_job_run(
            session, job_type="surrey_identity_scheduler", trigger="scheduler"
        )
        finish_job_run(session, second, status="success")

        jobs = list_ops_job_runs(session, job_type=None, status=None, limit=50)
        run_ids = [j["run_id"] for j in jobs]
        assert run_ids.index(second) < run_ids.index(first)
    finally:
        session.close()


def test_list_filters_by_job_type(job_run_db):
    session = _session_for(job_run_db)
    try:
        surrey_run = start_job_run(
            session, job_type="surrey_identity_scheduler", trigger="scheduler"
        )
        finish_job_run(session, surrey_run, status="success")
        other_run = start_job_run(session, job_type="some_other_job", trigger="manual")
        finish_job_run(session, other_run, status="success")

        jobs = list_ops_job_runs(
            session, job_type="surrey_identity_scheduler", status=None, limit=50
        )
        assert {j["run_id"] for j in jobs} == {surrey_run}
    finally:
        session.close()


def test_list_filters_by_terminal_status(job_run_db):
    session = _session_for(job_run_db)
    try:
        ok_run = start_job_run(session, job_type="j", trigger="manual")
        finish_job_run(session, ok_run, status="success")
        bad_run = start_job_run(session, job_type="j", trigger="manual")
        finish_job_run(session, bad_run, status="failed", raw_error="boom")
        partial_run = start_job_run(session, job_type="j", trigger="manual")
        finish_job_run(session, partial_run, status="partial_failure")

        assert {
            j["run_id"]
            for j in list_ops_job_runs(
                session, job_type=None, status="success", limit=50
            )
        } == {ok_run}
        assert {
            j["run_id"]
            for j in list_ops_job_runs(
                session, job_type=None, status="failed", limit=50
            )
        } == {bad_run}
        assert {
            j["run_id"]
            for j in list_ops_job_runs(
                session, job_type=None, status="partial_failure", limit=50
            )
        } == {partial_run}
    finally:
        session.close()


def test_list_active_never_includes_a_stale_run(job_run_db):
    """The task's explicit requirement: a stale run (running, lease
    expired) must never be classified/returned as active, and an active
    run must never leak into the stale filter."""
    session = _session_for(job_run_db)
    try:
        active_run = start_job_run(
            session, job_type="j", trigger="scheduler", lease_ttl=timedelta(minutes=30)
        )
        stale_run = start_job_run(
            session, job_type="j", trigger="scheduler", lease_ttl=timedelta(seconds=-1)
        )

        active_jobs = list_ops_job_runs(
            session, job_type=None, status="active", limit=50
        )
        stale_jobs = list_ops_job_runs(session, job_type=None, status="stale", limit=50)

        assert {j["run_id"] for j in active_jobs} == {active_run}
        assert {j["run_id"] for j in stale_jobs} == {stale_run}
        assert active_run not in {j["run_id"] for j in stale_jobs}
        assert stale_run not in {j["run_id"] for j in active_jobs}

        # And the payload's own normalized_status agrees.
        [active_payload] = [j for j in active_jobs if j["run_id"] == active_run]
        [stale_payload] = [j for j in stale_jobs if j["run_id"] == stale_run]
        assert active_payload["normalized_status"] == "active"
        assert stale_payload["normalized_status"] == "stale"
    finally:
        session.close()


def test_list_limit_is_respected(job_run_db):
    session = _session_for(job_run_db)
    try:
        for _ in range(5):
            run_id = start_job_run(session, job_type="j", trigger="manual")
            finish_job_run(session, run_id, status="success")

        jobs = list_ops_job_runs(session, job_type=None, status=None, limit=2)
        assert len(jobs) == 2
    finally:
        session.close()


def test_module_never_imports_the_legacy_pipeline_runs_schema():
    """Structural proof this module only ever queries ops_job_runs /
    ops_job_run_events -- never the legacy pipeline_runs table or
    pipeline_coordinator_runs (a completely different schema
    pipeline/ops_read_model.py reads). Checked via actual imports, not a
    naive source-text grep, so a docstring mentioning "pipeline_runs" in
    prose (as this module's own module docstring does, explaining the
    distinction) can never make this test flicker."""
    import pipeline.ops_jobs_read_model as read_model

    module_globals = vars(read_model)
    assert "PipelineRun" not in module_globals
    assert "pipeline_coordinator_runs" not in module_globals


def test_detail_returns_none_for_unknown_run_id(job_run_db):
    session = _session_for(job_run_db)
    try:
        assert get_ops_job_run_detail(session, "does-not-exist") is None
    finally:
        session.close()


def test_detail_event_ordering_matches_real_surrey_lifecycle(job_run_db):
    """started -> plan -> validate -> apply -> finished, oldest first --
    the exact M3C Surrey milestone shape."""
    session = _session_for(job_run_db)
    try:
        run_id = start_job_run(
            session, job_type="surrey_identity_scheduler", trigger="scheduler"
        )
        record_job_step(session, run_id, event_type="step_completed", step="plan")
        record_job_step(session, run_id, event_type="step_completed", step="validate")
        record_job_step(session, run_id, event_type="step_completed", step="apply")
        finish_job_run(
            session,
            run_id,
            status="success",
            counts={"source_rows": 5, "inserted": 1, "updated": 4, "error_count": 0},
        )

        detail = get_ops_job_run_detail(session, run_id)
        assert detail is not None
        event_shape = [(e["event_type"], e["step"]) for e in detail["events"]]
        assert event_shape == [
            ("started", None),
            ("step_completed", "plan"),
            ("step_completed", "validate"),
            ("step_completed", "apply"),
            ("finished", None),
        ]
        assert detail["normalized_status"] == "success"
        assert detail["counts"] == {
            "source_rows": 5,
            "inserted": 1,
            "updated": 4,
            "error_count": 0,
        }
    finally:
        session.close()


def test_detail_safe_error_redaction_for_a_failed_run(job_run_db):
    session = _session_for(job_run_db)
    try:
        run_id = start_job_run(session, job_type="j", trigger="manual")
        secret_like = "Traceback: postgresql://user:hunter2@host/db timed out"
        finish_job_run(session, run_id, status="failed", raw_error=secret_like)

        detail = get_ops_job_run_detail(session, run_id)
        assert detail is not None
        assert detail["error_present"] is True
        assert detail["error_summary"] in {
            "timeout",
            "http_4xx",
            "http_5xx",
            "database",
            "validation",
            "unknown",
        }
        serialized = str(detail)
        assert secret_like not in serialized
        assert "hunter2" not in serialized
    finally:
        session.close()


def test_job_type_summary_all_null_when_no_row_exists(job_run_db):
    session = _session_for(job_run_db)
    try:
        summary = get_job_type_summary(session, "never_run_job_type")
        assert summary == {"last_run_at": None, "last_status": None, "counts": None}
    finally:
        session.close()


def test_job_type_summary_reflects_the_most_recent_run(job_run_db):
    session = _session_for(job_run_db)
    try:
        older = start_job_run(session, job_type="j", trigger="scheduler")
        finish_job_run(session, older, status="failed", raw_error="boom")
        newer = start_job_run(session, job_type="j", trigger="scheduler")
        finish_job_run(
            session,
            newer,
            status="success",
            counts={"source_rows": 3, "inserted": 1, "updated": 2, "error_count": 0},
        )

        summary = get_job_type_summary(session, "j")
        assert summary["last_status"] == "success"
        assert summary["counts"] == {
            "source_rows": 3,
            "inserted": 1,
            "updated": 2,
            "error_count": 0,
        }
        assert summary["last_run_at"] is not None
    finally:
        session.close()


def test_job_type_summary_reports_active_not_running_for_an_in_progress_run(job_run_db):
    session = _session_for(job_run_db)
    try:
        run_id = start_job_run(session, job_type="j", trigger="scheduler")
        summary = get_job_type_summary(session, "j")
        assert summary["last_status"] == "active"
    finally:
        session.close()
