"""Contract tests for scripts/run_classification_claims_schema_audit.py (Class A).

Mirrors the established pattern used by
tests/unit/test_run_evidence_link_readiness_audit_script.py and
tests/unit/test_run_derived_tender_evidence_audit_script.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_classification_claims_schema_audit.py"


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip(
            "Refusing schema audit script tests against production DATABASE_URL"
        )
    return database_url


def test_stdout_is_pure_json_banner_on_stderr_guard_before_session():
    database_url = _require_local_database_url()
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.stdout.strip(), "expected stdout output"
    payload = json.loads(proc.stdout)  # raises if stdout is not pure JSON
    assert "status" in payload
    assert "Target Database" in proc.stderr
    assert "Nominal Class: Class A" in proc.stderr


def test_module_does_not_import_init_db() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "init_db" not in source


def test_guard_readonly_db_is_class_a_and_never_blocks_production() -> None:
    from db.classification import SafetyClass
    from db.db_safety import guard_readonly_db

    assert guard_readonly_db.__module__ == "db.db_safety"
    source = SCRIPT.read_text(encoding="utf-8")
    assert "guard_readonly_db(" in source
    assert "guard_destructive_db" not in source
    assert "guard_registry_write_db" not in source
    assert SafetyClass.A.label.lower().startswith("class a")
