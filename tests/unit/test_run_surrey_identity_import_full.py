"""Safety-contract tests for the PR-EN1F-5 full Class-C Surrey import
apply runner (scripts/run_surrey_identity_import_full.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.run_surrey_identity_import_full as runner
from db.classification import SafetyClass


def _artifact(**overrides):
    payload = {
        "artifact_schema_version": 1,
        "git_commit_sha": "a" * 40,
        "source": "surrey",
        "transaction_mode": "REPEATABLE READ, READ ONLY",
        "counts": {
            "source_total": 1194,
            "invalid_rows": 0,
            "duplicate_source_rows": 0,
            "production_total": 8279,
            "planned_updates": 930,
            "planned_inserts": 264,
            "duplicate_risk": 0,
            "blank_applicant_preserved": 0,
        },
        "plan_digest": "b" * 64,
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- artifact validation matrix ------------------------------------------


def test_valid_artifact_is_accepted(tmp_path):
    payload = _artifact()
    loaded = runner.load_and_validate_artifact(
        _write(tmp_path / "artifact.json", payload),
        current_git_sha="a" * 40,
    )
    assert loaded == payload


def test_missing_or_malformed_artifact_is_rejected(tmp_path):
    with pytest.raises(runner.SurreyImportFullApplyError):
        runner.load_and_validate_artifact(
            tmp_path / "missing.json",
            current_git_sha="a" * 40,
        )
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(runner.SurreyImportFullApplyError):
        runner.load_and_validate_artifact(path, current_git_sha="a" * 40)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_schema_version", 2),
        ("git_commit_sha", "d" * 40),
        ("source", "vancouver"),
        ("transaction_mode", "READ WRITE"),
        ("plan_digest", "not-a-digest"),
        ("plan_digest", "A" * 64),
    ],
)
def test_artifact_contract_mismatches_are_rejected(tmp_path, field, value):
    payload = _artifact()
    payload[field] = value
    with pytest.raises(runner.SurreyImportFullApplyError):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


@pytest.mark.parametrize(
    "field", ["invalid_rows", "duplicate_source_rows", "duplicate_risk"]
)
def test_unsafe_risk_counter_is_rejected(tmp_path, field):
    payload = _artifact()
    payload["counts"][field] = 1
    with pytest.raises(runner.SurreyImportFullApplyError, match=field):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


def test_zero_planned_work_is_rejected_as_stale(tmp_path):
    payload = _artifact()
    payload["counts"]["planned_updates"] = 0
    payload["counts"]["planned_inserts"] = 0
    with pytest.raises(runner.SurreyImportFullApplyError, match="stale"):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


def test_updates_only_or_inserts_only_is_accepted(tmp_path):
    payload = _artifact()
    payload["counts"]["planned_updates"] = 0
    payload["counts"]["planned_inserts"] = 1
    runner.load_and_validate_artifact(
        _write(tmp_path / "updates_only.json", payload),
        current_git_sha="a" * 40,
    )
    payload2 = _artifact()
    payload2["counts"]["planned_updates"] = 1
    payload2["counts"]["planned_inserts"] = 0
    runner.load_and_validate_artifact(
        _write(tmp_path / "inserts_only.json", payload2),
        current_git_sha="a" * 40,
    )


def test_negative_integer_field_is_rejected(tmp_path):
    payload = _artifact()
    payload["counts"]["planned_updates"] = -1
    with pytest.raises(runner.SurreyImportFullApplyError):
        runner.load_and_validate_artifact(
            _write(tmp_path / "artifact.json", payload),
            current_git_sha="a" * 40,
        )


# --- execute_full_import: commit/rollback contract -------------------------


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_execute_full_import_commits_exact_reviewed_counts(monkeypatch):
    session = _Session()
    captured = {}

    def fake_apply(_session, **kwargs):
        captured.update(kwargs)
        return {
            "eligible_updates": 930,
            "eligible_inserts": 264,
            "updated": 930,
            "inserted": 264,
            "plan_digest": "b" * 64,
        }

    monkeypatch.setattr(runner, "apply_surrey_identity_import_full", fake_apply)
    result = runner.execute_full_import(
        session, source_rows=[{"external_id": "x"}], artifact=_artifact()
    )
    assert result["updated"] == 930
    assert result["inserted"] == 264
    assert captured["expected_plan_digest"] == "b" * 64
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.parametrize(
    "result",
    [
        {
            "eligible_updates": 930,
            "eligible_inserts": 264,
            "updated": 929,
            "inserted": 264,
        },
        {
            "eligible_updates": 930,
            "eligible_inserts": 264,
            "updated": 930,
            "inserted": 263,
        },
        {
            "eligible_updates": 930,
            "eligible_inserts": 264,
            "updated": 931,
            "inserted": 264,
        },
        {
            "eligible_updates": 930,
            "eligible_inserts": 264,
            "updated": 930,
            "inserted": 265,
        },
        {
            "eligible_updates": 929,
            "eligible_inserts": 264,
            "updated": 930,
            "inserted": 264,
        },
        {
            "eligible_updates": 930,
            "eligible_inserts": 263,
            "updated": 930,
            "inserted": 264,
        },
        {"eligible_updates": 0, "eligible_inserts": 0, "updated": 0, "inserted": 0},
    ],
)
def test_execute_full_import_rolls_back_non_exact_result(monkeypatch, result):
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_surrey_identity_import_full",
        lambda *_args, **_kwargs: result,
    )
    with pytest.raises(runner.SurreyImportFullApplyError):
        runner.execute_full_import(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_full_import_rolls_back_writer_exception(monkeypatch):
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_surrey_identity_import_full",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    with pytest.raises(RuntimeError, match="secret"):
        runner.execute_full_import(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_full_import_rolls_back_on_stale_digest_from_live_refetch(monkeypatch):
    session = _Session()

    def fake_apply(_session, **_kwargs):
        from pipeline.surrey_identity_import_canary import (
            SurreyIdentityImportCanaryError,
        )

        raise SurreyIdentityImportCanaryError(
            "candidate set changed since the reviewed dry-run artifact"
        )

    monkeypatch.setattr(runner, "apply_surrey_identity_import_full", fake_apply)
    with pytest.raises(Exception, match="candidate set changed"):
        runner.execute_full_import(session, source_rows=[], artifact=_artifact())
    assert session.commits == 0
    assert session.rollbacks == 1


def test_execute_full_import_does_not_hardcode_expected_counts(monkeypatch):
    """The runner must read expected counts from the artifact, not a
    hardcoded 930/264 -- prove it commits correctly against a completely
    different artifact shape (e.g. after further production drift)."""
    session = _Session()
    monkeypatch.setattr(
        runner,
        "apply_surrey_identity_import_full",
        lambda *_args, **_kwargs: {
            "eligible_updates": 3,
            "eligible_inserts": 1,
            "updated": 3,
            "inserted": 1,
            "plan_digest": "c" * 64,
        },
    )
    small_artifact = _artifact(plan_digest="c" * 64)
    small_artifact["counts"]["planned_updates"] = 3
    small_artifact["counts"]["planned_inserts"] = 1
    result = runner.execute_full_import(
        session, source_rows=[], artifact=small_artifact
    )
    assert result["updated"] == 3
    assert result["inserted"] == 1
    assert session.commits == 1
    assert session.rollbacks == 0


# --- compute_result_digest --------------------------------------------------


def test_result_digest_differs_from_plan_digest_and_is_sensitive_to_counts():
    plan_digest = "d" * 64
    result_digest = runner.compute_result_digest(
        plan_digest=plan_digest, updated=930, inserted=264
    )
    assert len(result_digest) == 64
    assert result_digest != plan_digest
    other = runner.compute_result_digest(
        plan_digest=plan_digest, updated=930, inserted=263
    )
    assert other != result_digest


# --- script contract / wiring ---------------------------------------------


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
        if "run_surrey_identity_import_full.py" in line
    )
    assert "| C | Yes | No |" in row


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
    assert "import full apply" in captured["operation"]


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
    with pytest.raises(runner.SurreyImportFullApplyError):
        runner.main()


def test_main_rejects_production_apply_without_real_tty(monkeypatch, tmp_path):
    """End-to-end wiring proof: with the real (unmocked) guard,
    --allow-production against a non-TTY pytest stdin must refuse before
    any write, regardless of artifact validity."""
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


def test_main_writes_only_aggregates_no_raw_data_on_success(monkeypatch, tmp_path):
    payload = _artifact()
    payload["git_commit_sha"] = "9" * 40
    path = _write(tmp_path / "artifact.json", payload)
    output_path = tmp_path / "result.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--apply",
            "--artifact-path",
            str(path),
            "--output-path",
            str(output_path),
        ],
    )
    monkeypatch.setattr(runner, "get_git_commit_sha", lambda: "9" * 40)
    monkeypatch.setattr(runner, "guard_destructive_db_from_args", lambda *a, **k: "url")
    secret_number = "26-999999-001-00/SECRET"
    monkeypatch.setattr(
        runner,
        "fetch_official_source_rows",
        lambda: [{"external_id": secret_number, "applicant": "SECRET BUILDER"}],
    )
    monkeypatch.setattr(runner, "get_session", lambda: _Session())
    monkeypatch.setattr(
        runner,
        "apply_surrey_identity_import_full",
        lambda *_args, **_kwargs: {
            "eligible_updates": 930,
            "eligible_inserts": 264,
            "updated": 930,
            "inserted": 264,
            "plan_digest": payload["plan_digest"],
        },
    )
    assert runner.main() == 0

    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["updated"] == 930
    assert output["inserted"] == 264
    assert output["plan_digest"] == payload["plan_digest"]
    assert len(output["result_digest"]) == 64
    assert output["result_digest"] != output["plan_digest"]
    serialized = json.dumps(output)
    assert secret_number not in serialized
    assert "SECRET BUILDER" not in serialized
