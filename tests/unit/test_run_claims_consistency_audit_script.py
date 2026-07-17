"""Contract tests for scripts/run_claims_consistency_audit.py (Class A).

Mirrors the established pattern used by
tests/unit/test_run_classification_claims_schema_audit_script.py.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_claims_consistency_audit.py"


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
            "Refusing consistency audit script tests against production DATABASE_URL"
        )
    return database_url


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "_run_claims_consistency_audit_under_test", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdout_is_pure_json_banner_on_stderr_guard_before_session():
    """This script's queries assume the migration-029 tables exist (unlike
    the structural schema audit, which tolerates a missing table) -- apply
    it locally first so this is deterministic standalone, not dependent on
    another test file having left the schema applied."""
    from sqlalchemy import create_engine, text

    from db.classification_claims_ddl import classification_claims_table_names
    from db.classification_claims_migration import apply_classification_claims_migration

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    def _drop_all():
        with engine.begin() as conn:
            for name in classification_claims_table_names():
                conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))

    _drop_all()
    apply_classification_claims_migration(engine)
    try:
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
        assert payload["schema_version"] == 1
    finally:
        _drop_all()
        engine.dispose()
    assert "Target Database" in proc.stderr
    assert "Nominal Class: Class A" in proc.stderr
    assert "Environment: LOCAL" in proc.stderr


def test_use_production_banner_stays_on_stderr_and_stdout_stays_pure_json(
    monkeypatch, capsys
):
    """--use-production must route the guard banner to stderr and keep
    stdout pure JSON -- proven via mocks (fake host, no real connection,
    local or production)."""
    module = _load_script_module()

    fake_prod_url = "postgresql://user:pass@fake-prod-host.test:5432/fakedb"
    monkeypatch.setenv("DB_PRODUCTION_HOSTS", "fake-prod-host.test")
    monkeypatch.setenv("DATABASE_URL_PRODUCTION", fake_prod_url)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/local_db")

    fake_engine = MagicMock()
    monkeypatch.setattr(module, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(
        module,
        "run_claims_consistency_audit",
        lambda engine: {
            "status": "PASS",
            "findings": [],
            "counts": {"claims": 0, "evidence": 0, "events": 0, "rule_sets": 0},
            "dataset_hash": "a" * 64,
            "schema_version": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--use-production"])

    exit_code = module.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert "Target Database" in captured.err
    assert "Environment: PRODUCTION" in captured.err
    assert "Target Database" not in captured.out


def test_guard_is_called_before_get_engine(monkeypatch):
    module = _load_script_module()
    call_order: list[str] = []

    def fake_guard(args, *, script_name):
        call_order.append("guard")
        return "postgresql://user:pass@localhost:5432/local_db"

    def fake_get_engine():
        call_order.append("get_engine")
        return MagicMock()

    monkeypatch.setattr(module, "guard_readonly_db_from_args", fake_guard)
    monkeypatch.setattr(module, "get_engine", fake_get_engine)
    monkeypatch.setattr(
        module,
        "run_claims_consistency_audit",
        lambda engine: {
            "status": "PASS",
            "findings": [],
            "counts": {"claims": 0, "evidence": 0, "events": 0, "rule_sets": 0},
            "dataset_hash": "a" * 64,
            "schema_version": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    module.main()

    assert call_order == ["guard", "get_engine"]


def test_argparse_defines_use_production_and_not_allow_production():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            flags.add(node.args[0].value)

    assert "--use-production" in flags
    assert "--allow-production" not in flags


def test_module_does_not_import_init_db() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "init_db" not in source


def test_guard_readonly_db_is_class_a_and_never_blocks_production() -> None:
    from db.classification import SafetyClass
    from db.db_safety import guard_readonly_db_from_args

    assert guard_readonly_db_from_args.__module__ == "db.db_safety"
    source = SCRIPT.read_text(encoding="utf-8")
    assert "guard_readonly_db_from_args(" in source
    assert "guard_destructive_db" not in source
    assert "guard_registry_write_db" not in source
    assert SafetyClass.A.label.lower().startswith("class a")


def test_exit_code_reflects_audit_status(monkeypatch):
    module = _load_script_module()
    monkeypatch.setattr(
        module, "guard_readonly_db_from_args", lambda args, *, script_name: "x"
    )
    monkeypatch.setattr(module, "get_engine", lambda: MagicMock())
    monkeypatch.setattr(
        module,
        "run_claims_consistency_audit",
        lambda engine: {
            "status": "FAIL",
            "findings": ["something is wrong"],
            "counts": {"claims": 1, "evidence": 0, "events": 0, "rule_sets": 0},
            "dataset_hash": "a" * 64,
            "schema_version": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    assert module.main() == 1
