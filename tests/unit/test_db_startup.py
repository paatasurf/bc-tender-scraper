"""Unit tests for non-blocking DB startup and connection timeouts."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from db import connection


class _CaptureConnection:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def __enter__(self) -> "_CaptureConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object) -> None:
        self._statements.append(str(statement))


class _CaptureEngine:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def begin(self) -> _CaptureConnection:
        return _CaptureConnection(self.statements)


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


def test_tender_matches_table_init_skips_obsolete_unique_index() -> None:
    engine = _CaptureEngine()

    connection._ensure_tender_matches_table(engine)

    statements = "\n".join(engine.statements)
    assert "ix_tender_matches_company_id" in statements
    assert "ix_tender_matches_tender_id" in statements
    assert "ix_tender_matches_company_tender" not in statements


def test_tender_matches_company_kind_migration_dedupes_before_unique_index() -> None:
    engine = _CaptureEngine()

    connection._migrate_tender_matches_company_kind(engine)

    statements = "\n".join(engine.statements)
    dedupe_pos = statements.index("ROW_NUMBER() OVER")
    unique_pos = statements.index("ix_tender_matches_company_kind_tender")
    assert "DROP INDEX IF EXISTS ix_tender_matches_company_tender" in statements
    assert "PARTITION BY company_kind, company_id, tender_source, tender_id" in statements
    assert dedupe_pos < unique_pos


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
