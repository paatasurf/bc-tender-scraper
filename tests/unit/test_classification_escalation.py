"""Tests for runtime class escalation and dry-run provenance."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db.classification import SafetyClass
from db.db_safety import begin_script_guard, effective_class, require_runtime_class_d
from db.merge_dry_run_provenance import verify_dry_run_artifact


def test_class_b_escalates_to_d_on_init_db(capsys) -> None:
    url = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("db.db_safety.load_app_env"):
        with patch("db.db_safety.resolve_script_database_url", return_value=url):
            with patch("db.db_safety.apply_script_database_url", return_value=url):
                begin_script_guard(SafetyClass.B, "demo_class_b_escalation.py")
                assert effective_class() == SafetyClass.B
                require_runtime_class_d("init_db()")
                assert effective_class() == SafetyClass.D
    err = capsys.readouterr().err
    assert "Runtime escalation to Class D" in err


def test_init_db_no_guard_context_is_noop() -> None:
    with patch("db.db_safety._authorize_class_d") as auth:
        require_runtime_class_d("init_db()")
        auth.assert_not_called()


def test_stale_dry_run_refuses_commit_mismatch(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "dry_run_provenance": {
                    "git_commit_sha": "deadbeef",
                    "dataset_fingerprint": "abc123",
                }
            }
        ),
        encoding="utf-8",
    )
    session = MagicMock()
    with patch("db.merge_dry_run_provenance.get_git_commit_sha", return_value="cafebabe"):
        with patch("db.merge_dry_run_provenance.compute_dataset_fingerprint", return_value="abc123"):
            with pytest.raises(SystemExit) as exc:
                verify_dry_run_artifact(session=session, report_path=report)
            assert exc.value.code == 1
