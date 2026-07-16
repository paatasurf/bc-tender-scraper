"""Contract tests for scripts/run_classification_claims_migration.py (Class D).

Structural/guard tests only — this script's actual apply/rollback DDL
behavior is covered end-to-end by
tests/unit/test_classification_claims_migration.py against the underlying
db.classification_claims_migration functions directly. These tests confirm
the CLI wiring itself: argument mutual exclusivity, no auto-wiring into
init_db, and that the dry-run path never applies schema changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_classification_claims_migration.py"


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing migration script tests against production DATABASE_URL")
    return database_url


def test_module_does_not_import_init_db() -> None:
    """AST-based: checks for an actual import or call, not the documentation
    string this script writes into its own dry-run report ("not_wired_to":
    [..., "db.connection.init_db()"]) explaining that it deliberately does
    NOT call init_db — a substring check would false-positive on that."""
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "init_db" not in {alias.name for alias in node.names}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "init_db"


def test_script_not_wired_into_run_migrations() -> None:
    """Static guard: db.connection._run_migrations() must not reference this
    script's migration or DDL modules — the schema must stay inert until an
    operator explicitly runs this CLI."""
    connection_source = (ROOT / "db" / "connection.py").read_text(encoding="utf-8")
    assert "classification_claims" not in connection_source


def test_argparse_rejects_combining_dry_run_and_apply():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--apply"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "only one of" in result.stderr.lower()


def test_argparse_rejects_combining_apply_and_rollback():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--apply", "--rollback"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "only one of" in result.stderr.lower()


def test_dry_run_writes_artifact_and_does_not_apply_schema(tmp_path):
    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS rule_set_versions CASCADE"))

    artifact_path = tmp_path / "dryrun.json"
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", str(artifact_path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["class"] == "D"
    assert payload["dry_run"] is True
    assert payload["migration"] == "029_classification_claims"
    assert "not_wired_to" in payload

    with engine.begin() as conn:
        exists = conn.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'rule_set_versions'
                """)).first()
    assert exists is None, "dry-run must never apply schema DDL"
    engine.dispose()


_MIGRATION_SQL_PATH = ROOT / "db" / "migrations" / "029_classification_claims.sql"
_ALL_TABLE_NAMES = [
    "resolved_company_beliefs",
    "projector_runs",
    "claim_events",
    "claim_evidence",
    "classification_claims",
    "rule_set_versions",
]


def _drop_all_claims_tables(engine) -> None:
    with engine.begin() as conn:
        for name in _ALL_TABLE_NAMES:
            conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))


def test_apply_refuses_when_ddl_digest_is_stale(tmp_path):
    """Item #2: changing the DDL after a dry-run artifact is generated must
    block --apply as stale, even though git_commit_sha and schema-existence
    state are both unchanged. A whitespace-only edit is used so the DDL's
    executed semantics are untouched -- only its canonical digest changes."""
    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")
    _drop_all_claims_tables(engine)

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url

    artifact_path = tmp_path / "dryrun.json"
    dry_run_result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", str(artifact_path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert dry_run_result.returncode == 0, dry_run_result.stderr
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert "ddl_digest" in payload and payload["ddl_digest"]

    original_sql = _MIGRATION_SQL_PATH.read_text(encoding="utf-8")
    try:
        mutated_sql = original_sql.replace(
            "CREATE TABLE IF NOT EXISTS rule_set_versions (",
            "CREATE TABLE IF NOT EXISTS rule_set_versions ( ",
            1,
        )
        assert (
            mutated_sql != original_sql
        ), "fixture SQL did not contain the expected anchor line"
        _MIGRATION_SQL_PATH.write_text(mutated_sql, encoding="utf-8")

        apply_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--apply",
                "--artifact-path",
                str(artifact_path),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert apply_result.returncode != 0
        assert "stale" in apply_result.stderr.lower()
        assert (
            "ddl_digest" in apply_result.stderr.lower()
            or "ddl changed" in apply_result.stderr.lower()
        )
    finally:
        _MIGRATION_SQL_PATH.write_text(original_sql, encoding="utf-8")

    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'rule_set_versions'"
            )
        ).first()
    assert exists is None, "a refused apply must not have created any table"
    engine.dispose()


def test_artifact_path_flag_requires_apply():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--artifact-path", "unused.json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "--artifact-path" in result.stderr
    assert "--apply" in result.stderr


def test_apply_with_artifact_path_reads_the_matching_custom_dry_run(tmp_path):
    """Item #5: --dry-run PATH followed by --apply --artifact-path PATH must
    succeed against the exact artifact written to that custom path."""
    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")
    _drop_all_claims_tables(engine)

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url

    custom_path = tmp_path / "custom_dryrun.json"
    dry_run_result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", str(custom_path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert dry_run_result.returncode == 0, dry_run_result.stderr
    assert custom_path.is_file()

    try:
        apply_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--apply",
                "--artifact-path",
                str(custom_path),
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert apply_result.returncode == 0, apply_result.stderr

        with engine.begin() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'rule_set_versions'"
                )
            ).first()
        assert (
            exists is not None
        ), "apply via --artifact-path must have created the schema"
    finally:
        _drop_all_claims_tables(engine)
        engine.dispose()


def test_apply_without_artifact_path_ignores_a_custom_path_dry_run(tmp_path):
    """A dry-run written to a CUSTOM path must not be silently picked up by
    a bare --apply, which reads the DEFAULT artifact path. Proves the two
    flags are a real contract, not a coincidental match."""
    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")
    _drop_all_claims_tables(engine)

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url

    default_artifact = (
        ROOT / "exports" / "classification_claims_migration_dryrun_class_d.json"
    )
    saved_default: str | None = None
    if default_artifact.is_file():
        saved_default = default_artifact.read_text(encoding="utf-8")
        default_artifact.unlink()

    custom_path = tmp_path / "custom_dryrun_only.json"
    try:
        dry_run_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", str(custom_path)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert dry_run_result.returncode == 0, dry_run_result.stderr
        assert custom_path.is_file()
        assert not default_artifact.is_file()

        apply_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--apply"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert apply_result.returncode != 0
        assert "stale" in apply_result.stderr.lower()
        assert "missing artifact" in apply_result.stderr.lower()
    finally:
        if saved_default is not None:
            default_artifact.parent.mkdir(parents=True, exist_ok=True)
            default_artifact.write_text(saved_default, encoding="utf-8")
        _drop_all_claims_tables(engine)
        engine.dispose()
