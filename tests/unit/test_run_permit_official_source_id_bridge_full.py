"""Safety-contract tests for the PR-EN1E-4 Class-C full bridge-apply
runner (scripts/run_permit_official_source_id_bridge_full.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.run_permit_official_source_id_bridge_full as runner
from db.classification import SafetyClass


def _artifact(**overrides):
    payload = {
        "artifact_schema_version": 1,
        "git_commit_sha": "a" * 40,
        "source": "surrey",
        "transaction_mode": "REPEATABLE READ, READ ONLY",
        "counts": {
            "recoverable_official_source_id": 925,
            "invalid_source_ids": 0,
            "duplicate_source_ids": 0,
            "ambiguous_legacy_prefixes": 0,
            "duplicate_legacy_prefix_rows": 0,
            "ambiguous_production_external_ids": 0,
            "duplicate_production_legacy_ids": 0,
        },
        "candidate_count": 925,
        "candidate_set_digest": "b" * 64,
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_artifact_is_accepted(tmp_path):
    payload = _artifact()
    loaded = runner.load_and_validate_artifact(
        _write(tmp_path / "artifact.json", payload),
        current_git_sha="a" * 40,
    )
    assert loaded == payload


def test_missing_or_malformed_artifact_is_rejected(tmp_path):
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError):
        runner.load_and_validate_artifact(
            tmp_path / "missing.json",
            current_git_sha="a" * 40,
        )
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError):
        runner.load_and_validate_artifact(path, current_git_sha="a" * 40)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_schema_version", 2),
        ("git_commit_sha", "d" * 40),
        ("source", "vancouver"),
        ("transaction_mode", "READ WRITE"),
        ("candidate_set_digest", "not-a-digest"),
        ("candidate_set_digest", "A" * 64),
    ],
)
def test_artifact_contract_mismatches_are_rejected(tmp_path, field, value):
    payload = _artifact()
    payload[field] = value
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


@pytest.mark.parametrize(
    "field",
    [
        "invalid_source_ids",
        "duplicate_source_ids",
        "ambiguous_legacy_prefixes",
        "duplicate_legacy_prefix_rows",
        "ambiguous_production_external_ids",
        "duplicate_production_legacy_ids",
    ],
)
def test_unsafe_source_or_key_condition_is_rejected(tmp_path, field):
    payload = _artifact()
    payload["counts"][field] = 1
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError, match=field):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


def test_candidate_count_invariant_is_rejected(tmp_path):
    payload = _artifact()
    payload["counts"]["recoverable_official_source_id"] = 924
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError, match="invariant"):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


def test_zero_candidate_count_is_rejected_as_stale(tmp_path):
    payload = _artifact(candidate_count=0)
    payload["counts"]["recoverable_official_source_id"] = 0
    payload["candidate_set_digest"] = (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError, match="stale"):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


def test_negative_candidate_count_is_rejected(tmp_path):
    payload = _artifact(candidate_count=-1)
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_execute_full_bridge_commits_exact_candidate_count(monkeypatch):
    session = _Session()
    captured = {}

    def fake_apply(_session, **kwargs):
        captured.update(kwargs)
        return {"eligible_count": 925, "selected_count": 925, "updated_count": 925}

    monkeypatch.setattr(runner, "apply_permit_official_source_id_bridge", fake_apply)
    result = runner.execute_full_bridge(
        session,
        source_rows=[{"safe": "in-memory"}],
        artifact=_artifact(),
    )
    assert result["updated_count"] == 925
    assert captured["candidate_limit"] == 925
    assert captured["expected_candidate_set_digest"] == "b" * 64
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.parametrize(
    "result",
    [
        {"eligible_count": 925, "selected_count": 924, "updated_count": 924},
        {"eligible_count": 925, "selected_count": 925, "updated_count": 924},
        {"eligible_count": 925, "selected_count": 926, "updated_count": 926},
        {"eligible_count": 924, "selected_count": 925, "updated_count": 925},
        {"eligible_count": 926, "selected_count": 925, "updated_count": 925},
    ],
)
def test_execute_full_bridge_rolls_back_non_exact_result(monkeypatch, result):
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_permit_official_source_id_bridge",
        lambda *_args, **_kwargs: result,
    )
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError):
        runner.execute_full_bridge(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_full_bridge_refuses_when_live_eligible_count_drifted(monkeypatch):
    """Reproduces the exact drift scenario proven necessary in PR-EN1D-4: an
    artifact reviewed 925 candidates, but the live re-plan at apply-time
    finds 926 eligible rows overall. The writer's own digest check only
    compares the first-925-by-id slice, so it can still match and
    successfully write those 925 rows -- this broader invariant must still
    catch the drift and refuse."""
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_permit_official_source_id_bridge",
        lambda *_args, **_kwargs: {
            "eligible_count": 926,
            "selected_count": 925,
            "updated_count": 925,
        },
    )
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError, match="eligible"):
        runner.execute_full_bridge(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_full_bridge_rolls_back_writer_exception(monkeypatch):
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_permit_official_source_id_bridge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    with pytest.raises(RuntimeError, match="secret"):
        runner.execute_full_bridge(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_full_bridge_rolls_back_on_stale_digest_from_live_refetch(monkeypatch):
    session = _Session()

    def fake_apply(_session, **_kwargs):
        from pipeline.permit_official_source_id_bridge import (
            PermitOfficialSourceIdBridgeError,
        )

        raise PermitOfficialSourceIdBridgeError(
            "candidate set changed since the reviewed dry-run artifact"
        )

    monkeypatch.setattr(runner, "apply_permit_official_source_id_bridge", fake_apply)
    with pytest.raises(Exception, match="candidate set changed"):
        runner.execute_full_bridge(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_script_has_no_configurable_limit_or_force_flag():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'add_argument("--sample-size"' not in source
    assert 'add_argument("--limit"' not in source
    assert 'add_argument("--force"' not in source
    assert "guard_destructive_db_from_args" in source
    classification = Path("scripts/CLASSIFICATION.md").read_text(encoding="utf-8")
    row = next(
        line
        for line in classification.splitlines()
        if "run_permit_official_source_id_bridge_full.py" in line
    )
    assert "| C | Yes | Yes |" in row


def test_main_reaches_class_c_guard_before_network_or_database(monkeypatch, tmp_path):
    payload = _artifact()
    payload["git_commit_sha"] = "e" * 40
    path = _write(tmp_path / "artifact.json", payload)
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
        ["runner", "--apply", "--artifact-path", str(path)],
    )
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "e" * 40)
    monkeypatch.setattr(runner, "guard_destructive_db_from_args", fake_guard)
    monkeypatch.setattr(
        runner,
        "fetch_official_source_rows",
        lambda: (_ for _ in ()).throw(AssertionError("network touched")),
    )
    monkeypatch.setattr(
        runner,
        "get_session",
        lambda: (_ for _ in ()).throw(AssertionError("database touched")),
    )
    with pytest.raises(_GuardReached):
        runner.main()
    assert captured["nominal_class"] is SafetyClass.C
    assert "official-source-identity bridge" in captured["operation"]


def test_main_requires_apply_flag(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["runner"])
    with pytest.raises(SystemExit):
        runner.main()
    err = capsys.readouterr().err
    assert "--apply is required" in err


def test_main_refuses_without_valid_artifact_before_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--apply", "--artifact-path", str(tmp_path / "missing.json")],
    )
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "e" * 40)

    def unreachable_guard(*_args, **_kwargs):
        raise AssertionError("guard reached without a valid artifact")

    monkeypatch.setattr(runner, "guard_destructive_db_from_args", unreachable_guard)
    with pytest.raises(runner.PermitOfficialSourceIdBridgeFullError):
        runner.main()


def test_main_rejects_production_apply_without_real_tty(monkeypatch, tmp_path):
    """End-to-end wiring proof: with the real (unmocked) guard, --allow-production
    against a non-TTY pytest stdin must refuse before any write, regardless of
    artifact validity."""
    payload = _artifact()
    payload["git_commit_sha"] = "f" * 40
    path = _write(tmp_path / "artifact.json", payload)

    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--apply", "--allow-production", "--artifact-path", str(path)],
    )
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "f" * 40)
    monkeypatch.setattr(
        "db.db_safety.resolve_script_database_url",
        lambda **_kwargs: "postgresql://user:pass@production.rlwy.net:5432/railway",
    )
    monkeypatch.setattr(
        "db.db_safety.apply_script_database_url",
        lambda **_kwargs: "postgresql://user:pass@production.rlwy.net:5432/railway",
    )
    monkeypatch.setattr(
        runner,
        "fetch_official_source_rows",
        lambda: (_ for _ in ()).throw(AssertionError("network touched")),
    )
    monkeypatch.setattr(
        runner,
        "get_session",
        lambda: (_ for _ in ()).throw(AssertionError("database touched")),
    )
    with pytest.raises(SystemExit):
        runner.main()
