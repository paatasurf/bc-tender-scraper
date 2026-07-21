"""Safety-contract tests for the migration 031 Class-D runner
(scripts/run_permit_official_source_id_migration.py, PR-EN1E-1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.run_permit_official_source_id_migration as runner
from db.classification import SafetyClass


def _stats(*, pending: bool) -> dict:
    return {
        "column_exists": not pending,
        "index_exists": not pending,
        "migration_pending": pending,
        "statements_planned": 2,
    }


def _artifact(*, sha: str, digest: str, before: dict) -> dict:
    return {
        "operation": "permit_official_source_id_schema_migration",
        "class": "D",
        "dry_run": True,
        "generated_at": "2026-07-21T00:00:00+00:00",
        "git_commit_sha": sha,
        "ddl_digest": digest,
        "artifact_path": "irrelevant.json",
        "migration": "031_permit_official_source_id",
        "production_before": before,
        "planned_mutations": {
            "destructive_delete": False,
            "ddl_only": True,
            "statements_planned": 2,
            "already_applied": False,
        },
        "ddl_plan": {"statements": []},
        "apply_command_preview": "irrelevant",
        "not_wired_to": [],
    }


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_digest() -> str:
    return runner.permit_official_source_id_ddl_digest()


class _Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_verify_dry_run_artifact_accepts_a_fresh_matching_artifact(
    tmp_path, monkeypatch
):
    sha = "a" * 40
    before = _stats(pending=True)
    path = _write(
        tmp_path / "artifact.json",
        _artifact(sha=sha, digest=_valid_digest(), before=before),
    )
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: sha)
    monkeypatch.setattr(
        runner, "permit_official_source_id_before_stats", lambda _s: before
    )
    runner._verify_dry_run_artifact(session=object(), report_path=path)  # no SystemExit


def test_verify_dry_run_artifact_rejects_missing_file(tmp_path):
    with pytest.raises(SystemExit):
        runner._verify_dry_run_artifact(
            session=object(), report_path=tmp_path / "missing.json"
        )


def test_verify_dry_run_artifact_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner._verify_dry_run_artifact(session=object(), report_path=path)


def test_verify_dry_run_artifact_rejects_git_sha_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "e" * 40)
    path = _write(
        tmp_path / "artifact.json",
        _artifact(sha="d" * 40, digest=_valid_digest(), before=_stats(pending=True)),
    )
    with pytest.raises(SystemExit):
        runner._verify_dry_run_artifact(session=object(), report_path=path)


def test_verify_dry_run_artifact_rejects_malformed_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "e" * 40)
    path = _write(
        tmp_path / "artifact.json",
        _artifact(sha="e" * 40, digest="not-a-digest", before=_stats(pending=True)),
    )
    with pytest.raises(SystemExit):
        runner._verify_dry_run_artifact(session=object(), report_path=path)


def test_verify_dry_run_artifact_rejects_stale_ddl_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "e" * 40)
    path = _write(
        tmp_path / "artifact.json",
        _artifact(sha="e" * 40, digest="0" * 64, before=_stats(pending=True)),
    )
    with pytest.raises(SystemExit):
        runner._verify_dry_run_artifact(session=object(), report_path=path)


def test_verify_dry_run_artifact_rejects_stale_schema_state(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "e" * 40)
    path = _write(
        tmp_path / "artifact.json",
        _artifact(sha="e" * 40, digest=_valid_digest(), before=_stats(pending=True)),
    )
    # Schema now shows fully applied -- artifact was generated before that
    # changed, so it must be treated as stale.
    monkeypatch.setattr(
        runner,
        "permit_official_source_id_before_stats",
        lambda _s: _stats(pending=False),
    )
    with pytest.raises(SystemExit):
        runner._verify_dry_run_artifact(session=object(), report_path=path)


def test_dry_run_and_apply_are_mutually_exclusive(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--dry-run", str(tmp_path / "x.json"), "--apply"],
    )
    with pytest.raises(SystemExit):
        runner.main()


def test_artifact_path_requires_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--artifact-path", str(tmp_path / "x.json")],
    )
    with pytest.raises(SystemExit):
        runner.main()


def test_apply_reaches_class_d_guard_before_artifact_check_network_or_database(
    monkeypatch, tmp_path
):
    """Mirrors run_company_track_record_migration.py's order: the Class D
    guard runs first, before the dry-run artifact is even read."""
    captured = {}

    class _GuardReached(Exception):
        pass

    def fake_guard(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        raise _GuardReached

    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--apply", "--artifact-path", str(tmp_path / "never-read.json")],
    )
    monkeypatch.setattr(runner, "guard_destructive_db_from_args", fake_guard)
    monkeypatch.setattr(
        runner,
        "_verify_dry_run_artifact",
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("artifact touched before guard")
        ),
    )
    monkeypatch.setattr(
        runner,
        "get_session",
        lambda: (_ for _ in ()).throw(AssertionError("database touched before guard")),
    )
    monkeypatch.setattr(
        runner,
        "get_engine",
        lambda: (_ for _ in ()).throw(AssertionError("engine touched before guard")),
    )
    with pytest.raises(_GuardReached):
        runner.main()
    assert captured["nominal_class"] is SafetyClass.D
    assert "031" in captured["operation"]


def test_apply_refuses_after_guard_when_artifact_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner, "guard_destructive_db_from_args", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--apply", "--artifact-path", str(tmp_path / "missing.json")],
    )
    monkeypatch.setattr(
        runner,
        "get_session",
        lambda: _Session(),
    )
    with pytest.raises(SystemExit):
        runner.main()


def test_dry_run_writes_artifact_with_expected_shape(monkeypatch, tmp_path):
    artifact_path = tmp_path / "dry_run.json"
    session = _Session()

    monkeypatch.setattr(sys, "argv", ["runner", "--dry-run", str(artifact_path)])
    monkeypatch.setattr(runner, "guard_readonly_db_from_args", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "get_session", lambda: session)
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "2" * 40)
    monkeypatch.setattr(
        runner,
        "permit_official_source_id_before_stats",
        lambda _s: _stats(pending=True),
    )

    runner.main()

    assert session.closed is True
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["class"] == "D"
    assert payload["migration"] == "031_permit_official_source_id"
    assert payload["git_commit_sha"] == "2" * 40
    assert runner.is_valid_ddl_digest(payload["ddl_digest"])
    assert payload["not_wired_to"] == [
        "db.connection._run_migrations()",
        "db.connection.init_db()",
    ]
