"""Contract tests for scripts/run_classification_claims_schema_audit.py (Class A).

Mirrors the established pattern used by
tests/unit/test_run_evidence_link_readiness_audit_script.py and
tests/unit/test_run_derived_tender_evidence_audit_script.py.

Production-target tests use mocks/fake hosts only -- this file never opens a
connection to DATABASE_URL_PRODUCTION, per the no-production-access
constraint for this fix.
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


def _load_script_module():
    """Load the script as an importable module without executing __main__.

    module_from_spec/exec_module runs under the module's own __name__ (not
    "__main__"), so the `if __name__ == "__main__":` guard at the bottom of
    the script never fires -- safe to load and then call .main() explicitly.
    """
    spec = importlib.util.spec_from_file_location(
        "_run_classification_claims_schema_audit_under_test", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_conformance():
    conformance = MagicMock()
    conformance.tables = {}
    conformance.describe_violations.return_value = []
    return conformance


# --- local-target integration (real local Postgres, no flag) -----------------------


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
    assert "Environment: LOCAL" in proc.stderr, "no --use-production must select LOCAL"


# --- CLI flag wiring / production target (mocks only, no DB connection) ------------


def test_no_flag_selects_local_database_url(monkeypatch):
    """Without --use-production, guard_readonly_db_from_args must resolve
    the local DATABASE_URL, not DATABASE_URL_PRODUCTION."""
    from db.db_safety import guard_readonly_db_from_args

    local_url = "postgresql://user:pass@localhost:5432/local_db"
    monkeypatch.setenv("DATABASE_URL", local_url)
    monkeypatch.setenv(
        "DATABASE_URL_PRODUCTION", "postgresql://user:pass@fake-prod-host.test/fakedb"
    )

    args = __import__("argparse").Namespace(use_production=False)
    resolved = guard_readonly_db_from_args(
        args, script_name="run_classification_claims_schema_audit.py"
    )

    assert resolved == local_url
    assert os.environ["DATABASE_URL"] == local_url


def test_use_production_flag_is_forwarded_and_selects_database_url_production(
    monkeypatch,
):
    """--use-production must reach guard_readonly_db_from_args and resolve
    DATABASE_URL_PRODUCTION -- verified via a fake, clearly-non-real host
    (DB_PRODUCTION_HOSTS override), never a real production URL. No
    connection is attempted: apply_script_database_url only sets env vars
    and clears the (lazy) SQLAlchemy engine cache."""
    from db.db_safety import guard_readonly_db_from_args

    fake_prod_url = "postgresql://user:pass@fake-prod-host.test:5432/fakedb"
    monkeypatch.setenv("DB_PRODUCTION_HOSTS", "fake-prod-host.test")
    monkeypatch.setenv("DATABASE_URL_PRODUCTION", fake_prod_url)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/local_db")

    args = __import__("argparse").Namespace(use_production=True)
    resolved = guard_readonly_db_from_args(
        args, script_name="run_classification_claims_schema_audit.py"
    )

    assert resolved == fake_prod_url
    assert os.environ["DATABASE_URL"] == fake_prod_url


def test_use_production_banner_stays_on_stderr_and_stdout_stays_pure_json(
    monkeypatch, capsys
):
    """End-to-end main() with --use-production, fully mocked DB layer (fake
    host, no real connection, local or production) -- proves the banner
    still lands on stderr only and stdout is still pure JSON when the
    production flag is set."""
    module = _load_script_module()

    fake_prod_url = "postgresql://user:pass@fake-prod-host.test:5432/fakedb"
    monkeypatch.setenv("DB_PRODUCTION_HOSTS", "fake-prod-host.test")
    monkeypatch.setenv("DATABASE_URL_PRODUCTION", fake_prod_url)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/local_db")

    fake_session = MagicMock()
    monkeypatch.setattr(
        module, "get_session_factory", lambda: MagicMock(return_value=fake_session)
    )
    monkeypatch.setattr(
        module, "verify_schema_contract", lambda session: _fake_conformance()
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--use-production"])

    exit_code = module.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)  # stdout must be pure JSON
    assert payload["status"] == "PASS"
    assert "Target Database" in captured.err
    assert "Environment: PRODUCTION" in captured.err
    assert "Nominal Class: Class A" in captured.err
    assert "Target Database" not in captured.out
    assert "PRODUCTION" not in captured.out


def test_guard_is_called_before_session_factory(monkeypatch):
    """Call-order proof (not just source order): the guard must run and
    resolve the DB target before get_session_factory() is ever invoked."""
    module = _load_script_module()
    call_order: list[str] = []

    def fake_guard(args, *, script_name):
        call_order.append("guard")
        return "postgresql://user:pass@localhost:5432/local_db"

    def fake_get_session_factory():
        call_order.append("session_factory")
        return MagicMock(return_value=MagicMock())

    monkeypatch.setattr(module, "guard_readonly_db_from_args", fake_guard)
    monkeypatch.setattr(module, "get_session_factory", fake_get_session_factory)
    monkeypatch.setattr(
        module, "verify_schema_contract", lambda session: _fake_conformance()
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    module.main()

    assert call_order == ["guard", "session_factory"]


def test_argparse_defines_use_production_and_not_allow_production():
    """AST-based: checks actual argparse.add_argument(...) flag strings, not
    the module docstring (which explains, in prose, that --allow-production
    does not exist here -- a substring check would false-positive on that)."""
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
    assert "add_production_safety_args" not in SCRIPT.read_text(encoding="utf-8")


# --- safety: no writes, no DDL, no init_db wiring -----------------------------------


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


def test_no_write_or_ddl_sql_statements_are_executed() -> None:
    """Static guard: every `text(...)` SQL fragment in this script must be
    read-only (SELECT) -- no CREATE/ALTER/INSERT/UPDATE/DELETE/DROP/TRUNCATE
    statement is ever executed by this Class A audit."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    forbidden = (
        "CREATE ",
        "ALTER ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "TRUNCATE ",
    )
    checked = 0

    def _literal_text(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value for part in node.values if isinstance(part, ast.Constant)
            )
        return None

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
        ):
            sql = _literal_text(node.args[0])
            if sql is None:
                continue
            checked += 1
            upper = sql.strip().upper()
            assert upper.startswith("SELECT"), f"non-SELECT SQL executed: {sql!r}"
            for keyword in forbidden:
                assert (
                    keyword not in upper
                ), f"forbidden keyword {keyword!r} in: {sql!r}"

    assert checked >= 1, "expected at least one text(...) SQL statement to check"
