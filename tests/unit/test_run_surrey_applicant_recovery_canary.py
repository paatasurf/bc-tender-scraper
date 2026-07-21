"""Safety-contract tests for the EN1D-3 Class-C canary runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.run_surrey_applicant_recovery_canary as runner
from db.classification import SafetyClass


def _artifact():
    return {
        "artifact_schema_version": 2,
        "git_commit_sha": "a" * 40,
        "source": "surrey",
        "transaction_mode": "REPEATABLE READ, READ ONLY",
        "counts": {
            "recoverable_blank_applicant": 438,
            "invalid_source_ids": 0,
            "duplicate_source_ids": 0,
            "ambiguous_legacy_prefixes": 0,
            "duplicate_legacy_prefix_rows": 0,
            "ambiguous_production_external_ids": 0,
        },
        "candidate_count": 438,
        "candidate_set_digest": "b" * 64,
        "recommended_canary_limit": 25,
        "canary_candidate_count": 25,
        "canary_candidate_set_digest": "c" * 64,
    }


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
    with pytest.raises(runner.SurreyApplicantCanaryError):
        runner.load_and_validate_artifact(
            tmp_path / "missing.json",
            current_git_sha="a" * 40,
        )
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(runner.SurreyApplicantCanaryError):
        runner.load_and_validate_artifact(path, current_git_sha="a" * 40)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_schema_version", 1),
        ("git_commit_sha", "d" * 40),
        ("source", "vancouver"),
        ("transaction_mode", "READ WRITE"),
        ("candidate_count", 24),
        ("recommended_canary_limit", 26),
        ("canary_candidate_count", 24),
        ("candidate_set_digest", "not-a-digest"),
        ("canary_candidate_set_digest", "A" * 64),
    ],
)
def test_artifact_contract_mismatches_are_rejected(tmp_path, field, value):
    payload = _artifact()
    payload[field] = value
    with pytest.raises(runner.SurreyApplicantCanaryError):
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
    ],
)
def test_unsafe_source_or_key_condition_is_rejected(tmp_path, field):
    payload = _artifact()
    payload["counts"][field] = 1
    with pytest.raises(runner.SurreyApplicantCanaryError, match=field):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


def test_candidate_count_invariant_is_rejected(tmp_path):
    payload = _artifact()
    payload["counts"]["recoverable_blank_applicant"] = 437
    with pytest.raises(runner.SurreyApplicantCanaryError, match="invariant"):
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


def test_execute_canary_commits_exactly_25(monkeypatch):
    session = _Session()
    captured = {}

    def fake_apply(_session, **kwargs):
        captured.update(kwargs)
        return {"selected_count": 25, "updated_count": 25}

    monkeypatch.setattr(runner, "apply_surrey_applicant_recovery", fake_apply)
    result = runner.execute_canary(
        session,
        source_rows=[{"safe": "in-memory"}],
        artifact=_artifact(),
    )
    assert result["updated_count"] == 25
    assert captured["candidate_limit"] == 25
    assert captured["expected_candidate_set_digest"] == "c" * 64
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.parametrize(
    "result",
    [
        {"selected_count": 24, "updated_count": 24},
        {"selected_count": 25, "updated_count": 24},
    ],
)
def test_execute_canary_rolls_back_non_exact_result(monkeypatch, result):
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_surrey_applicant_recovery",
        lambda *_args, **_kwargs: result,
    )
    with pytest.raises(runner.SurreyApplicantCanaryError):
        runner.execute_canary(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_canary_rolls_back_writer_exception(monkeypatch):
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_surrey_applicant_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    with pytest.raises(RuntimeError, match="secret"):
        runner.execute_canary(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_script_is_fixed_25_row_class_c_runner():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'add_argument("--sample-size"' not in source
    assert 'add_argument("--limit"' not in source
    assert "CANARY_LIMIT = 25" in source
    assert "guard_destructive_db_from_args" in source
    classification = Path("scripts/CLASSIFICATION.md").read_text(encoding="utf-8")
    row = next(
        line
        for line in classification.splitlines()
        if "run_surrey_applicant_recovery_canary.py" in line
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
    assert "exactly 25 rows" in captured["operation"]
