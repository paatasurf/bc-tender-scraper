from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_data_coverage_audit.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("run_dq1a_transaction_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Transaction:
    def __init__(self) -> None:
        self.rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True


class _Connection:
    def __init__(self) -> None:
        self.transaction = _Transaction()
        self.statements: list[str] = []
        self.closed = False

    def begin(self) -> _Transaction:
        return self.transaction

    def execute(self, statement):
        self.statements.append(str(statement))

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def connect(self) -> _Connection:
        return self.connection


class _Session:
    def __init__(self, *, bind) -> None:
        self.bind = bind
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_run_audit_rolls_back_and_writes_after_success(monkeypatch, tmp_path) -> None:
    module = _load_script()
    engine = _Engine()
    sessions: list[_Session] = []

    def session_factory(*, bind):
        session = _Session(bind=bind)
        sessions.append(session)
        return session

    monkeypatch.setattr(module, "Session", session_factory)
    monkeypatch.setattr(
        module,
        "audit_data_coverage",
        lambda session, as_of: {
            "as_of": as_of.isoformat(),
            "datasets": {},
            "findings": [],
            "finding_counts": {},
            "dataset_digests": {},
        },
    )
    monkeypatch.setattr(module, "get_git_commit_sha", lambda: "a" * 40)
    artifact_path = tmp_path / "audit.json"
    module.run_audit(
        engine,
        artifact_path=artifact_path,
        as_of=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    assert engine.connection.statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ]
    assert sessions[0].closed
    assert engine.connection.transaction.rolled_back
    assert engine.connection.closed
    assert artifact_path.exists()


def test_run_audit_failure_rolls_back_and_writes_no_artifact(
    monkeypatch, tmp_path
) -> None:
    module = _load_script()
    engine = _Engine()
    monkeypatch.setattr(module, "Session", _Session)

    def fail(*args, **kwargs):
        raise RuntimeError("injected secret")

    monkeypatch.setattr(module, "audit_data_coverage", fail)
    artifact_path = tmp_path / "must-not-exist.json"
    with pytest.raises(RuntimeError, match="injected secret"):
        module.run_audit(
            engine,
            artifact_path=artifact_path,
            as_of=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
    assert engine.connection.transaction.rolled_back
    assert engine.connection.closed
    assert not artifact_path.exists()
