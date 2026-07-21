"""Runner contract tests for Surrey applicant recovery dry-run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_surrey_applicant_recovery_dryrun as runner


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Http:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_source_fetch_paginates_and_requests_only_required_fields(monkeypatch):
    http = _Http()
    calls = []
    responses = iter(
        [
            _Response({"maxRecordCount": 1}),
            _Response({"count": 2}),
            _Response(
                {
                    "features": [
                        {
                            "attributes": {
                                "PermitNumber": "26-111111-001-00/ABC",
                                "ApplicantOrganization": "A Ltd.",
                                "ProjectAddress": "must not escape",
                            }
                        }
                    ],
                    "exceededTransferLimit": True,
                }
            ),
            _Response(
                {
                    "features": [
                        {
                            "attributes": {
                                "PermitNumber": "26-222222-001-00/ABC",
                                "ApplicantOrganization": None,
                            }
                        }
                    ],
                    "exceededTransferLimit": False,
                }
            ),
        ]
    )

    def fake_get(_http, url, *, params):
        calls.append((url, params))
        return next(responses)

    monkeypatch.setattr(runner, "create_session", lambda: http)
    monkeypatch.setattr(runner, "polite_api_get", fake_get)

    rows = runner.fetch_official_source_rows()

    assert len(rows) == 2
    assert set(rows[0]) == {"PermitNumber", "ApplicantOrganization"}
    assert calls[1][1]["returnCountOnly"] == "true"
    assert calls[2][1]["outFields"] == runner.SOURCE_FIELDS
    assert calls[2][1]["resultOffset"] == 0
    assert calls[3][1]["resultOffset"] == 1
    assert http.closed is True


def test_source_fetch_closes_http_session_on_failure(monkeypatch):
    http = _Http()
    monkeypatch.setattr(runner, "create_session", lambda: http)
    monkeypatch.setattr(
        runner,
        "polite_api_get",
        lambda *_args, **_kwargs: _Response({"error": {"message": "secret"}}),
    )
    with pytest.raises(RuntimeError, match="metadata request failed"):
        runner.fetch_official_source_rows()
    assert http.closed is True


def test_source_fetch_fails_closed_when_pagination_ends_early(monkeypatch):
    http = _Http()
    responses = iter(
        [
            _Response({"maxRecordCount": 500}),
            _Response({"count": 2}),
            _Response({"features": []}),
        ]
    )
    monkeypatch.setattr(runner, "create_session", lambda: http)
    monkeypatch.setattr(
        runner,
        "polite_api_get",
        lambda *_args, **_kwargs: next(responses),
    )
    with pytest.raises(RuntimeError, match="before the advertised count"):
        runner.fetch_official_source_rows()
    assert http.closed is True


class _Transaction:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


class _Connection:
    def __init__(self):
        self.transaction = _Transaction()
        self.statements = []
        self.closed = False

    def begin(self):
        return self.transaction

    def execute(self, statement):
        self.statements.append(str(statement))

    def close(self):
        self.closed = True


class _Engine:
    def __init__(self):
        self.connection = _Connection()
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return self.connection


class _AuditSession:
    def __init__(self, *, bind):
        self.bind = bind
        self.closed = False

    def close(self):
        self.closed = True


def test_run_uses_one_read_only_transaction_and_rolls_back(monkeypatch, tmp_path):
    engine = _Engine()
    sessions = []

    def session_factory(*, bind):
        session = _AuditSession(bind=bind)
        sessions.append(session)
        return session

    monkeypatch.setattr(runner, "Session", session_factory)
    monkeypatch.setattr(
        runner,
        "plan_surrey_applicant_recovery",
        lambda session, *, source_rows: {
            "counts": {"source_total": len(source_rows)},
            "candidate_count": 0,
            "candidate_set_digest": "0" * 64,
        },
    )
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "abc123")
    path = tmp_path / "artifact.json"

    artifact = runner.run_dry_run(
        engine,
        source_rows=[],
        artifact_path=path,
    )

    assert engine.connect_calls == 1
    assert engine.connection.statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ]
    assert sessions[0].closed is True
    assert engine.connection.transaction.rolled_back is True
    assert engine.connection.closed is True
    assert artifact["transaction_mode"] == runner.TRANSACTION_MODE
    assert path.exists()


def test_failure_rolls_back_and_writes_no_artifact(monkeypatch, tmp_path):
    engine = _Engine()
    monkeypatch.setattr(runner, "Session", _AuditSession)
    monkeypatch.setattr(
        runner,
        "plan_surrey_applicant_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    path = tmp_path / "artifact.json"

    with pytest.raises(RuntimeError, match="secret"):
        runner.run_dry_run(engine, source_rows=[], artifact_path=path)

    assert engine.connection.transaction.rolled_back is True
    assert engine.connection.closed is True
    assert not path.exists()


def test_script_has_no_write_mode_or_destructive_guard():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'add_argument("--apply"' not in source
    assert 'add_argument("--allow-production"' not in source
    assert "guard_destructive_db_from_args" not in source
    assert "guard_readonly_db_from_args" in source
    for forbidden in (
        "session.add(",
        "session.commit(",
        "session.flush(",
        "sqlalchemy.insert(",
        "sqlalchemy.update(",
        "sqlalchemy.delete(",
    ):
        assert forbidden not in source


def test_classification_lists_runner_as_class_a():
    classification = Path("scripts/CLASSIFICATION.md").read_text(encoding="utf-8")
    row = next(
        line
        for line in classification.splitlines()
        if "run_surrey_applicant_recovery_dryrun.py" in line
    )
    assert "| A |" in row
    assert "no `--apply`/`--allow-production`" in row
