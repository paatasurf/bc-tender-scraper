"""Tests for pipeline/job_run_telemetry.py -- the shared fail-open session
wrapper generalized (M3D-A) from pipeline/scheduler.py's original
Surrey-only implementation (M3C). See tests/unit/test_surrey_job_run_telemetry.py
and tests/unit/test_scheduler.py for the regression proof that Surrey's
own behavior (including exact log text) is unchanged by this refactor --
those files were not modified for M3D-A.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pipeline.job_run_telemetry import (
    call_with_telemetry_session,
    close_telemetry_session,
)


def test_close_telemetry_session_noop_when_session_is_none():
    close_telemetry_session(None)  # must not raise


def test_close_telemetry_session_calls_close_on_a_real_session():
    session = MagicMock()
    close_telemetry_session(session)
    session.close.assert_called_once()


def test_close_telemetry_session_swallows_a_close_failure():
    session = MagicMock()
    session.close.side_effect = RuntimeError("boom")
    close_telemetry_session(session)  # must not raise


def test_call_with_telemetry_session_returns_fn_result_on_success(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr("db.connection.get_session", MagicMock(return_value=session))

    result = call_with_telemetry_session(
        lambda s: "the-result",
        log_label="X telemetry",
        failure_message="failed to do X",
    )

    assert result == "the-result"
    session.close.assert_called_once()


def test_call_with_telemetry_session_closes_the_session_it_acquired(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr("db.connection.get_session", MagicMock(return_value=session))

    call_with_telemetry_session(
        lambda s: None, log_label="X telemetry", failure_message="failed"
    )

    session.close.assert_called_once()


def test_call_with_telemetry_session_returns_none_and_logs_when_get_session_raises(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
    )

    with caplog.at_level("WARNING"):
        result = call_with_telemetry_session(
            lambda s: "unreachable",
            log_label="X telemetry",
            failure_message="failed to do X",
        )

    assert result is None
    assert "X telemetry: failed to do X" in caplog.text
    assert "db down" not in caplog.text


def test_call_with_telemetry_session_returns_none_and_closes_session_when_fn_raises(
    monkeypatch, caplog
):
    session = MagicMock()
    monkeypatch.setattr("db.connection.get_session", MagicMock(return_value=session))

    def _raising(s):
        raise RuntimeError("secret-looking-failure: postgresql://user:hunter2@host/db")

    with caplog.at_level("WARNING"):
        result = call_with_telemetry_session(
            _raising, log_label="X telemetry", failure_message="failed to do X"
        )

    assert result is None
    session.close.assert_called_once()
    assert "X telemetry: failed to do X" in caplog.text
    assert "hunter2" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_call_with_telemetry_session_never_closes_a_session_it_never_acquired(
    monkeypatch,
):
    monkeypatch.setattr(
        "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
    )
    # No session object exists to assert .close() on -- the real proof is
    # simply that this never raises AttributeError/etc. trying to close
    # something that was never acquired.
    result = call_with_telemetry_session(
        lambda s: "unreachable", log_label="X telemetry", failure_message="failed"
    )
    assert result is None
