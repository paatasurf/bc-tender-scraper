"""Tests for M3C: ENABLE_SURREY_JOB_RUN_TELEMETRY-gated instrumentation of
pipeline.scheduler._scheduled_surrey_identity_run(). Covers the flag off/
on behavior, success/partial_failure/failed status mapping, fail-open
telemetry, and that no plan_digest/result_digest/raw error text ever
reaches the pipeline.job_run writer calls.

All DB access (both the "real" Surrey session and every telemetry
session) is mocked -- these are pure unit tests, no local Postgres
needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import pipeline.scheduler as scheduler_module
from pipeline.surrey_identity_scheduler import SurreyIdentitySchedulerResult


class _FakeSurreySession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _patch_common(monkeypatch, *, rows=None, get_session_return=None):
    """Common plumbing: fake row source, and db.connection.get_session()
    returning a fresh MagicMock (with a no-op .close()) every call, used
    by both the real Surrey session and every telemetry helper's own
    session acquisition."""
    monkeypatch.setattr(
        "scraper.surrey_permits.iter_surrey_permits",
        lambda *, days: iter(rows or [{"external_id": "26-000001-001-00/AB"}]),
    )
    sessions_created: list[object] = []

    def _fake_get_session():
        session = get_session_return() if get_session_return else MagicMock()
        sessions_created.append(session)
        return session

    monkeypatch.setattr("db.connection.get_session", _fake_get_session)
    return sessions_created


def _success_result(**overrides) -> SurreyIdentitySchedulerResult:
    base = dict(
        source_rows=2,
        updated=1,
        inserted=1,
        errors=0,
        plan_digest="a" * 64,
        result_digest="b" * 64,
    )
    base.update(overrides)
    return SurreyIdentitySchedulerResult(**base)


# ---------------------------------------------------------------------
# flag=false: writer completely untouched
# ---------------------------------------------------------------------


def test_flag_false_calls_no_writer_function_at_all(monkeypatch):
    monkeypatch.delenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, raising=False)
    _patch_common(monkeypatch)

    for name in (
        "start_job_run",
        "heartbeat_job_run",
        "record_job_step",
        "finish_job_run",
    ):
        monkeypatch.setattr(
            scheduler_module,
            name,
            MagicMock(side_effect=AssertionError(f"{name} must not be called")),
        )

    monkeypatch.setattr(
        scheduler_module,
        "run_surrey_identity_import_once",
        lambda *_a, **_k: _success_result(),
    )

    scheduler_module._scheduled_surrey_identity_run()  # must not raise


def test_flag_explicitly_false_also_calls_no_writer(monkeypatch):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "false")
    _patch_common(monkeypatch)
    for name in (
        "start_job_run",
        "heartbeat_job_run",
        "record_job_step",
        "finish_job_run",
    ):
        monkeypatch.setattr(
            scheduler_module,
            name,
            MagicMock(side_effect=AssertionError(f"{name} must not be called")),
        )
    monkeypatch.setattr(
        scheduler_module,
        "run_surrey_identity_import_once",
        lambda *_a, **_k: _success_result(),
    )
    scheduler_module._scheduled_surrey_identity_run()


# ---------------------------------------------------------------------
# flag=true: success
# ---------------------------------------------------------------------


def test_flag_true_success_records_start_phase_events_and_finish(monkeypatch):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)

    start_mock = MagicMock(return_value="run-123")
    heartbeat_mock = MagicMock()
    step_mock = MagicMock()
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "start_job_run", start_mock)
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", heartbeat_mock)
    monkeypatch.setattr(scheduler_module, "record_job_step", step_mock)
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    def fake_run_once(_session, *, rows, on_phase=None):
        if on_phase is not None:
            on_phase("plan")
            on_phase("validate")
            on_phase("apply")
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    scheduler_module._scheduled_surrey_identity_run()

    start_mock.assert_called_once()
    _, start_kwargs = start_mock.call_args
    assert start_kwargs["job_type"] == "surrey_identity_scheduler"
    assert start_kwargs["trigger"] == "scheduler"
    assert (
        "idempotency_key" not in start_kwargs or start_kwargs["idempotency_key"] is None
    )

    assert step_mock.call_count == 3
    recorded_steps = [call.kwargs.get("step") for call in step_mock.call_args_list]
    assert recorded_steps == ["plan", "validate", "apply"]
    for call in step_mock.call_args_list:
        assert call.kwargs.get("event_type") == "step_completed"
        assert call.args[1] == "run-123"  # run_id passed through positionally

    assert heartbeat_mock.call_count == 3

    finish_mock.assert_called_once()
    _, finish_kwargs = finish_mock.call_args
    assert finish_kwargs["status"] == "success"
    assert finish_kwargs["counts"] == {
        "source_rows": 1,
        "inserted": 1,
        "updated": 1,
        "error_count": 0,
    }
    assert finish_kwargs.get("raw_error") is None


# ---------------------------------------------------------------------
# flag=true: errors>0 -> partial_failure
# ---------------------------------------------------------------------


def test_flag_true_errors_present_maps_to_partial_failure(monkeypatch):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)

    monkeypatch.setattr(
        scheduler_module, "start_job_run", MagicMock(return_value="run-456")
    )
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", MagicMock())
    monkeypatch.setattr(scheduler_module, "record_job_step", MagicMock())
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    blocked_result = SurreyIdentitySchedulerResult(
        source_rows=3,
        updated=0,
        inserted=0,
        errors=1,
        plan_digest="c" * 64,
        result_digest=None,
    )
    monkeypatch.setattr(
        scheduler_module,
        "run_surrey_identity_import_once",
        lambda *_a, **_k: blocked_result,
    )

    scheduler_module._scheduled_surrey_identity_run()

    finish_mock.assert_called_once()
    _, finish_kwargs = finish_mock.call_args
    assert finish_kwargs["status"] == "partial_failure"
    assert finish_kwargs["counts"]["error_count"] == 1


# ---------------------------------------------------------------------
# flag=true: exception -> failed, raw error never leaked
# ---------------------------------------------------------------------


def test_flag_true_exception_from_scrape_maps_to_failed_and_still_propagates(
    monkeypatch,
):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    monkeypatch.setattr(
        scheduler_module, "start_job_run", MagicMock(return_value="run-789")
    )
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", MagicMock())
    monkeypatch.setattr(scheduler_module, "record_job_step", MagicMock())
    monkeypatch.setattr("db.connection.get_session", MagicMock())

    secret = "postgresql://user:hunter2@host/db permit-secret"

    def raising_iter(*, days):
        raise RuntimeError(secret)

    monkeypatch.setattr("scraper.surrey_permits.iter_surrey_permits", raising_iter)

    with pytest.raises(RuntimeError, match="hunter2"):
        scheduler_module._scheduled_surrey_identity_run()

    finish_mock.assert_called_once()
    _, finish_kwargs = finish_mock.call_args
    assert finish_kwargs["status"] == "failed"
    # raw_error IS passed to finish_job_run (which safely classifies it) --
    # that's the only place it's allowed to go.
    assert finish_kwargs.get("raw_error") == secret


def test_flag_true_exception_from_import_once_maps_to_failed_and_propagates(
    monkeypatch,
):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        scheduler_module, "start_job_run", MagicMock(return_value="run-999")
    )
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", MagicMock())
    monkeypatch.setattr(scheduler_module, "record_job_step", MagicMock())

    def raising_run_once(*_a, **_k):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", raising_run_once
    )

    with pytest.raises(RuntimeError, match="unexpected failure"):
        scheduler_module._scheduled_surrey_identity_run()

    finish_mock.assert_called_once()
    _, finish_kwargs = finish_mock.call_args
    assert finish_kwargs["status"] == "failed"


# ---------------------------------------------------------------------
# fail-open: telemetry itself failing never blocks the real Surrey work
# ---------------------------------------------------------------------


def test_start_failure_still_runs_the_surrey_worker_exactly_once(monkeypatch, caplog):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)

    monkeypatch.setattr(
        scheduler_module,
        "start_job_run",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "heartbeat_job_run",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "record_job_step",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "finish_job_run",
        MagicMock(side_effect=RuntimeError("db down")),
    )

    worker_calls = []

    def fake_run_once(_session, *, rows, on_phase=None):
        worker_calls.append(1)
        if on_phase is not None:
            on_phase("plan")
            on_phase("validate")
            on_phase("apply")
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    with caplog.at_level("WARNING"):
        scheduler_module._scheduled_surrey_identity_run()  # must not raise

    assert len(worker_calls) == 1
    assert "db down" not in caplog.text  # no raw exception text leaked into logs


def test_heartbeat_step_and_finish_failures_still_run_the_surrey_worker_exactly_once(
    monkeypatch, caplog
):
    """Unlike the start-failure case, start_job_run() succeeds here (a
    real telemetry_run_id exists), so heartbeat_job_run/record_job_step/
    finish_job_run are all genuinely attempted -- and each independently
    fails. The Surrey worker must still run exactly once and the
    scheduled function must not raise."""
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)

    monkeypatch.setattr(
        scheduler_module, "start_job_run", MagicMock(return_value="run-fail-open")
    )
    monkeypatch.setattr(
        scheduler_module,
        "heartbeat_job_run",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "record_job_step",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "finish_job_run",
        MagicMock(side_effect=RuntimeError("db down")),
    )

    worker_calls = []

    def fake_run_once(_session, *, rows, on_phase=None):
        worker_calls.append(1)
        if on_phase is not None:
            on_phase("plan")
            on_phase("validate")
            on_phase("apply")
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    with caplog.at_level("WARNING"):
        scheduler_module._scheduled_surrey_identity_run()  # must not raise

    assert len(worker_calls) == 1
    assert "db down" not in caplog.text  # no raw exception text leaked into logs


def test_telemetry_start_failure_alone_disables_the_rest_of_telemetry_but_not_the_job(
    monkeypatch,
):
    """If start_job_run() itself fails, telemetry_run_id is None -- no
    phase/finish telemetry call is even attempted for this run, and the
    Surrey worker still runs normally."""
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)

    monkeypatch.setattr(
        scheduler_module,
        "start_job_run",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    heartbeat_mock = MagicMock()
    step_mock = MagicMock()
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", heartbeat_mock)
    monkeypatch.setattr(scheduler_module, "record_job_step", step_mock)
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    def fake_run_once(_session, *, rows, on_phase=None):
        if on_phase is not None:
            on_phase("plan")
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    scheduler_module._scheduled_surrey_identity_run()

    heartbeat_mock.assert_not_called()
    step_mock.assert_not_called()
    finish_mock.assert_not_called()


# ---------------------------------------------------------------------
# No plan_digest/result_digest/raw error text in any writer payload
# ---------------------------------------------------------------------


def test_no_digest_ever_appears_in_a_writer_call_argument(monkeypatch):
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    _patch_common(monkeypatch)

    start_mock = MagicMock(return_value="run-digest-check")
    step_mock = MagicMock()
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "start_job_run", start_mock)
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", MagicMock())
    monkeypatch.setattr(scheduler_module, "record_job_step", step_mock)
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    plan_digest = "d" * 64
    result_digest = "e" * 64

    def fake_run_once(_session, *, rows, on_phase=None):
        if on_phase is not None:
            on_phase("plan")
            on_phase("validate")
            on_phase("apply")
        return _success_result(
            source_rows=len(rows), plan_digest=plan_digest, result_digest=result_digest
        )

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    scheduler_module._scheduled_surrey_identity_run()

    all_calls = (
        start_mock.call_args_list
        + step_mock.call_args_list
        + finish_mock.call_args_list
    )
    for call in all_calls:
        serialized = str(call.args) + str(call.kwargs)
        assert plan_digest not in serialized
        assert result_digest not in serialized


# ---------------------------------------------------------------------
# Review fix: get_session() itself raising must not break Surrey (true
# fail-open -- get_session() now called INSIDE each helper's own try)
# ---------------------------------------------------------------------


def test_get_session_raising_during_telemetry_start_still_runs_worker_once(monkeypatch):
    """get_session() raises on the very first call (start_job_run's own
    session acquisition) -- telemetry_run_id ends up None, so no further
    telemetry is attempted, but the real Surrey session (the *next*
    get_session() call) must still succeed and the worker must still run
    normally."""
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    monkeypatch.setattr(
        "scraper.surrey_permits.iter_surrey_permits",
        lambda *, days: iter([{"external_id": "26-000001-001-00/AB"}]),
    )
    monkeypatch.setattr(
        "db.connection.get_session",
        MagicMock(side_effect=[RuntimeError("db down"), MagicMock()]),
    )

    # start_job_run itself is never reached (get_session raised first),
    # but patch it anyway to fail loudly if it somehow were called.
    monkeypatch.setattr(
        scheduler_module,
        "start_job_run",
        MagicMock(side_effect=AssertionError("must not be called")),
    )
    heartbeat_mock = MagicMock()
    step_mock = MagicMock()
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", heartbeat_mock)
    monkeypatch.setattr(scheduler_module, "record_job_step", step_mock)
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    worker_calls = []

    def fake_run_once(_session, *, rows, on_phase=None):
        worker_calls.append(1)
        assert on_phase is None  # telemetry disabled for this run
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    scheduler_module._scheduled_surrey_identity_run()  # must not raise

    assert len(worker_calls) == 1
    heartbeat_mock.assert_not_called()
    step_mock.assert_not_called()
    finish_mock.assert_not_called()


def test_get_session_raising_during_phase_still_runs_worker_once_and_finishes_success(
    monkeypatch, caplog
):
    """get_session() succeeds for start and the real Surrey session, but
    raises for the "plan" phase's own telemetry session. The worker must
    still complete normally (its own SurreyIdentitySchedulerResult is
    untouched) and finish_job_run must still record the real outcome."""
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    monkeypatch.setattr(
        "scraper.surrey_permits.iter_surrey_permits",
        lambda *, days: iter([{"external_id": "26-000001-001-00/AB"}]),
    )
    # Call order: 1) start_job_run's session, 2) the real Surrey session,
    # 3) "plan" phase session (raises), 4) "validate" phase session,
    # 5) "apply" phase session, 6) finish's session.
    monkeypatch.setattr(
        "db.connection.get_session",
        MagicMock(
            side_effect=[
                MagicMock(),
                MagicMock(),
                RuntimeError("db down"),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ]
        ),
    )

    monkeypatch.setattr(
        scheduler_module, "start_job_run", MagicMock(return_value="run-phase-fail")
    )
    heartbeat_mock = MagicMock()
    step_mock = MagicMock()
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", heartbeat_mock)
    monkeypatch.setattr(scheduler_module, "record_job_step", step_mock)
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    worker_calls = []

    def fake_run_once(_session, *, rows, on_phase=None):
        worker_calls.append(1)
        if on_phase is not None:
            on_phase("plan")  # this one's get_session() raises internally
            on_phase("validate")
            on_phase("apply")
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    with caplog.at_level("WARNING"):
        scheduler_module._scheduled_surrey_identity_run()  # must not raise

    assert len(worker_calls) == 1
    # "plan" phase's own get_session() failure was swallowed -- record_job_step/
    # heartbeat_job_run were never reached for that phase, but the other two
    # phases still went through normally.
    assert step_mock.call_count == 2
    assert heartbeat_mock.call_count == 2
    finish_mock.assert_called_once()
    _, finish_kwargs = finish_mock.call_args
    assert finish_kwargs["status"] == "success"  # the real Surrey outcome, untouched
    assert "db down" not in caplog.text


def test_get_session_raising_during_finish_still_runs_worker_once(monkeypatch, caplog):
    """get_session() succeeds everywhere except finish_job_run's own
    session acquisition. The worker must still have run exactly once and
    completed successfully -- the failed telemetry write is swallowed,
    never propagated."""
    monkeypatch.setenv(scheduler_module.SURREY_JOB_RUN_TELEMETRY_FLAG, "true")
    monkeypatch.setattr(
        "scraper.surrey_permits.iter_surrey_permits",
        lambda *, days: iter([{"external_id": "26-000001-001-00/AB"}]),
    )
    # 1) start, 2) real Surrey session, 3-5) plan/validate/apply phases,
    # 6) finish's own session -- raises.
    monkeypatch.setattr(
        "db.connection.get_session",
        MagicMock(
            side_effect=[
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                RuntimeError("db down"),
            ]
        ),
    )

    monkeypatch.setattr(
        scheduler_module, "start_job_run", MagicMock(return_value="run-finish-fail")
    )
    monkeypatch.setattr(scheduler_module, "heartbeat_job_run", MagicMock())
    monkeypatch.setattr(scheduler_module, "record_job_step", MagicMock())
    finish_mock = MagicMock()
    monkeypatch.setattr(scheduler_module, "finish_job_run", finish_mock)

    worker_calls = []

    def fake_run_once(_session, *, rows, on_phase=None):
        worker_calls.append(1)
        if on_phase is not None:
            on_phase("plan")
            on_phase("validate")
            on_phase("apply")
        return _success_result(source_rows=len(rows))

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    with caplog.at_level("WARNING"):
        scheduler_module._scheduled_surrey_identity_run()  # must not raise

    assert len(worker_calls) == 1
    # finish_job_run itself is never reached -- get_session() raised
    # before the call could happen.
    finish_mock.assert_not_called()
    assert "db down" not in caplog.text
    assert "failed to finish job run tracking" in caplog.text
