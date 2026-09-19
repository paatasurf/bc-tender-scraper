"""Unit tests for non-blocking DB startup and connection timeouts."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from db import connection


@pytest.fixture(autouse=True)
def _reset_db_init_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module-level init state between tests."""
    with connection._db_init_lock:
        connection._db_init_status = "pending"
        connection._db_init_error = None
        connection._db_init_started_at = None
        connection._db_init_completed_at = None
        connection._last_init_db_error = None
    monkeypatch.delenv("DB_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("DB_HEALTH_RETRIES", raising=False)
    monkeypatch.delenv("DB_HEALTH_RETRY_DELAY", raising=False)
    monkeypatch.delenv("DB_HEALTH_RETRY_MAX_DELAY", raising=False)


def test_engine_connect_args_includes_connect_timeout_for_railway() -> None:
    url = "postgresql://user:pass@postgres.railway.internal:5432/railway"
    args = connection._engine_connect_args(url)
    assert args["connect_timeout"] == 10
    assert args["sslmode"] == "require"


def test_engine_connect_args_includes_connect_timeout_for_localhost() -> None:
    url = "postgresql://user:pass@localhost:5432/app"
    args = connection._engine_connect_args(url)
    assert args["connect_timeout"] == 10
    assert "sslmode" not in args


def test_engine_connect_args_respects_db_connect_timeout_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "5")
    url = "postgresql://user:pass@localhost:5432/app"
    args = connection._engine_connect_args(url)
    assert args["connect_timeout"] == 5


def test_transient_db_error_includes_timeout_expired() -> None:
    assert "timeout expired" in connection.TRANSIENT_DB_ERROR_MARKERS


def test_db_health_retry_settings_are_short_by_default() -> None:
    assert connection.db_health_retry_settings() == (1, 0.5, 1.0)


def test_check_db_connection_uses_fast_single_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    engine = object()
    ping = MagicMock()

    def fake_run_with_db_retry(operation, **kwargs):
        calls.append(kwargs)
        operation()

    monkeypatch.setattr(connection, "get_engine", lambda: engine)
    monkeypatch.setattr(connection, "run_with_db_retry", fake_run_with_db_retry)
    monkeypatch.setattr(connection, "_verify_connection_once", ping)

    assert connection.check_db_connection() is True
    ping.assert_called_once_with(engine)
    assert len(calls) == 1
    assert calls[0]["context"] == "check_db_connection"
    assert calls[0]["retries"] == 1
    assert calls[0]["base_delay"] == 0.5
    assert calls[0]["max_delay"] == 1.0


def test_check_db_connection_returns_false_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run_with_db_retry(*_args, **_kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(connection, "run_with_db_retry", fail_run_with_db_retry)

    assert connection.check_db_connection() is False


def test_background_init_transitions_to_complete() -> None:
    with patch.object(connection, "init_db", return_value=True):
        connection.start_init_db_background()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status = connection.get_db_init_status()
            if status["status"] == "complete":
                break
            time.sleep(0.05)
        else:
            pytest.fail("background init did not reach complete")

    status = connection.get_db_init_status()
    assert status["status"] == "complete"
    assert status["error"] is None
    assert status["started_at"] is not None
    assert status["completed_at"] is not None


def test_background_init_transitions_to_failed() -> None:
    connection._last_init_db_error = "connection timeout expired"
    with patch.object(connection, "init_db", return_value=False):
        connection.start_init_db_background()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status = connection.get_db_init_status()
            if status["status"] == "failed":
                break
            time.sleep(0.05)
        else:
            pytest.fail("background init did not reach failed")

    status = connection.get_db_init_status()
    assert status["status"] == "failed"
    assert status["error"] == "connection timeout expired"


def test_start_init_db_background_is_single_flight() -> None:
    started = threading.Event()

    def slow_init(*_args: object, **_kwargs: object) -> bool:
        started.set()
        time.sleep(0.2)
        return True

    with patch.object(connection, "init_db", side_effect=slow_init):
        connection.start_init_db_background()
        assert started.wait(timeout=2.0)
        connection.start_init_db_background()
        assert connection.get_db_init_status()["status"] == "running"

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if connection.get_db_init_status()["status"] == "complete":
                break
            time.sleep(0.05)

    assert connection.get_db_init_status()["status"] == "complete"


def test_session_scope_closes_on_success_and_error() -> None:
    from unittest.mock import MagicMock

    from db.connection import session_scope

    factory = MagicMock()
    session = MagicMock()
    factory.return_value = session

    with patch.object(connection, "get_session_factory", return_value=factory):
        with session_scope() as s:
            assert s is session
        session.close.assert_called_once()

        session.reset_mock()
        with pytest.raises(RuntimeError, match="boom"):
            with session_scope():
                raise RuntimeError("boom")
        session.close.assert_called_once()
