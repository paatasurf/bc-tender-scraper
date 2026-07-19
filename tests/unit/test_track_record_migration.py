"""Focused tests for the PR-G2A migration-first scope:
  - db.track_record_ddl (statement parsing + digest, no DB)
  - db.track_record_schema_contract (contract data + conformance logic, no DB)
  - db.track_record_migration (readiness/apply, local Postgres only,
    transaction + rollback -- migration 030 is never persistently applied
    by these tests, locally or otherwise)
  - scripts/run_company_track_record_migration.py structural contract
    (no persistent DB mutation -- --apply is only exercised on its
    fail-fast, pre-DDL refusal paths)

db/models.py is deliberately untouched by PR-G2A -- these tests only
exercise raw-SQL / information_schema introspection, never the ORM.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from db.track_record_ddl import (
    company_track_record_check_constraint_names,
    company_track_record_column_names,
    company_track_record_ddl_digest,
    company_track_record_migration_statements,
    is_valid_ddl_digest,
)
from db.track_record_migration import (
    ApplyReadinessStatus,
    CompanyTrackRecordApplyPostconditionError,
    apply_company_track_record_migration,
    build_track_record_audit_report,
    company_track_record_apply_readiness,
    company_track_record_before_stats,
    company_track_record_migration_pending,
    company_track_record_nonnull_row_count,
)
from db.track_record_schema_contract import (
    TABLE_NAME,
    TRACK_RECORD_CHECK_CONSTRAINTS,
    TRACK_RECORD_COLUMNS,
    TrackRecordConformance,
    verify_track_record_schema_contract,
)
from tests.db_test_safety import require_local_test_database

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SCRIPT = ROOT / "scripts" / "run_company_track_record_migration.py"
AUDIT_SCRIPT = ROOT / "scripts" / "run_company_track_record_schema_audit.py"

# ===================================================================
# 1. DDL statement parsing + digest -- no DB
# ===================================================================


def test_migration_statements_count_is_five():
    """4 ADD COLUMN statements + 1 combined DO $$ ... END $$; block."""
    statements = company_track_record_migration_statements()
    assert len(statements) == 5


def test_do_block_is_captured_as_a_single_statement_not_split_internally():
    statements = company_track_record_migration_statements()
    do_blocks = [s for s in statements if s.strip().upper().startswith("DO $$")]
    assert len(do_blocks) == 1
    do_block = do_blocks[0]
    # All three ADD CONSTRAINT clauses must live inside this one statement --
    # proving the parser did not split at their internal semicolons.
    assert do_block.count("ADD CONSTRAINT") == 3
    assert do_block.rstrip().endswith("END $$;")


def test_add_column_statements_present_individually():
    statements = company_track_record_migration_statements()
    add_column_statements = [s for s in statements if "ADD COLUMN IF NOT EXISTS" in s]
    assert len(add_column_statements) == 4
    for name in company_track_record_column_names():
        assert any(name in s for s in add_column_statements), name


def test_ddl_digest_is_deterministic():
    assert company_track_record_ddl_digest() == company_track_record_ddl_digest()


def test_ddl_digest_is_valid_sha256():
    assert is_valid_ddl_digest(company_track_record_ddl_digest())


def test_ddl_digest_changes_when_statements_change(monkeypatch):
    import db.track_record_ddl as ddl_mod

    original = ddl_mod.company_track_record_ddl_digest()
    monkeypatch.setattr(
        ddl_mod,
        "company_track_record_migration_statements",
        lambda: [
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS example_col INTEGER NULL;"
        ],
    )
    changed = ddl_mod.company_track_record_ddl_digest()
    assert changed != original
    assert ddl_mod.is_valid_ddl_digest(changed)


@pytest.mark.parametrize(
    "bad", ["", "a" * 63, "a" * 65, "A" * 64, "not-a-hash", None, 12345, "g" * 64]
)
def test_is_valid_ddl_digest_rejects_malformed(bad):
    assert is_valid_ddl_digest(bad) is False


def test_check_constraint_names_match_schema_contract():
    assert set(company_track_record_check_constraint_names()) == {
        c.name for c in TRACK_RECORD_CHECK_CONSTRAINTS
    }


def test_column_names_match_schema_contract():
    assert set(company_track_record_column_names()) == {
        c.name for c in TRACK_RECORD_COLUMNS
    }


# ===================================================================
# 2. Schema contract data + conformance logic -- no DB
# ===================================================================


def test_table_name_is_companies():
    assert TABLE_NAME == "companies"


def test_four_columns_declared_all_nullable():
    assert len(TRACK_RECORD_COLUMNS) == 4
    assert all(c.is_nullable for c in TRACK_RECORD_COLUMNS)


def test_three_check_constraints_declared():
    assert len(TRACK_RECORD_CHECK_CONSTRAINTS) == 3


def test_conformance_true_when_everything_matches():
    conformance = TrackRecordConformance(columns_exist=True)
    assert conformance.conforms is True
    assert conformance.describe_violations() == []


def test_conformance_false_when_columns_do_not_exist():
    conformance = TrackRecordConformance(
        columns_exist=False, missing_columns=("a", "b")
    )
    assert conformance.conforms is False
    assert "no track_record_* columns exist" in conformance.describe_violations()[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("missing_columns", ("track_record_score",)),
        ("wrong_type_columns", ("track_record_score",)),
        ("wrong_nullability_columns", ("track_record_score",)),
        ("wrong_length_columns", ("track_record_version",)),
        ("columns_with_default", ("track_record_score",)),
        ("missing_check_constraints", ("ck_companies_track_record_score_range",)),
        ("wrong_check_constraints", ("ck_companies_track_record_score_range",)),
    ],
)
def test_conformance_false_for_each_violation_kind(field, value):
    conformance = TrackRecordConformance(columns_exist=True, **{field: value})
    assert conformance.conforms is False
    assert len(conformance.describe_violations()) == 1


# ===================================================================
# 3. DB-backed migration functions -- local Postgres only, rollback-only.
#    Skipped on CI and refused against production. Nothing is ever
#    committed: migration 030 is applied only inside a transaction that
#    is rolled back at the end of each test.
# ===================================================================


@pytest.fixture()
def local_conn():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    conn = engine.connect()
    outer = conn.begin()
    # ALTER TABLE companies ... ADD COLUMN needs an ACCESS EXCLUSIVE lock,
    # even for the IF NOT EXISTS case. In a full-suite run, some other
    # DB-integration test may leave a lock (or an idle-in-transaction
    # session) on companies; without a bound, this test would then hang
    # indefinitely waiting for that lock instead of failing fast.
    conn.execute(text("SET LOCAL lock_timeout = '10s'"))
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()
        engine.dispose()


def test_before_stats_shows_pending_before_apply(local_conn):
    stats = company_track_record_before_stats(local_conn)
    assert stats["migration_pending"] is True
    assert set(stats["columns_missing"]) == set(company_track_record_column_names())


def test_migration_pending_true_before_apply(local_conn):
    assert company_track_record_migration_pending(local_conn) is True


def test_apply_readiness_not_applied_before_apply(local_conn):
    readiness = company_track_record_apply_readiness(local_conn)
    assert readiness.status is ApplyReadinessStatus.NOT_APPLIED


def _apply_within_savepoint(conn) -> None:
    """Apply migration 030's statements against the connection's current
    (rolled-back-at-teardown) transaction -- never committed to the
    database outside the test's own transaction scope."""
    with conn.begin_nested():
        for statement in company_track_record_migration_statements():
            conn.execute(text(statement))


def test_before_stats_shows_not_pending_after_apply(local_conn):
    _apply_within_savepoint(local_conn)
    stats = company_track_record_before_stats(local_conn)
    assert stats["migration_pending"] is False
    assert stats["columns_missing"] == []


def test_apply_readiness_fully_applied_after_apply(local_conn):
    _apply_within_savepoint(local_conn)
    readiness = company_track_record_apply_readiness(local_conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED
    assert readiness.violations == []


def test_verify_schema_contract_conforms_after_apply(local_conn):
    _apply_within_savepoint(local_conn)
    conformance = verify_track_record_schema_contract(local_conn)
    assert conformance.conforms is True


def test_apply_is_idempotent_when_run_twice(local_conn):
    _apply_within_savepoint(local_conn)
    # Second application inside the same outer transaction must not raise.
    _apply_within_savepoint(local_conn)
    readiness = company_track_record_apply_readiness(local_conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED


def test_nonnull_row_count_returns_zero_when_columns_do_not_exist_yet(local_conn):
    """company_track_record_nonnull_row_count() now takes the caller's own
    connection directly (no engine/transaction of its own) -- called here
    against local_conn before migration 030 has been applied within it,
    where the columns really do not exist yet."""
    count = company_track_record_nonnull_row_count(local_conn)
    assert count == 0


def test_nonnull_row_count_zero_immediately_after_apply_same_connection(local_conn):
    """Reads through the exact same connection the apply happened on --
    since company_track_record_nonnull_row_count() no longer opens its own
    engine/transaction, it directly observes this fixture's still-open
    (uncommitted) transaction, proving no row is left non-NULL by a bare
    apply (no wiring PR has ever written to these columns)."""
    _apply_within_savepoint(local_conn)
    count = company_track_record_nonnull_row_count(local_conn)
    assert count == 0


def test_nonnull_row_count_reflects_same_connections_uncommitted_writes(local_conn):
    """Single-connection visibility proof: a value written in an
    uncommitted savepoint on local_conn IS seen by
    company_track_record_nonnull_row_count(local_conn) because it reads
    through that same connection -- it would NOT be seen if the function
    opened a separate connection/engine (as it did before this rework)."""
    _apply_within_savepoint(local_conn)
    name = f"PR-G2A Audit Test {uuid.uuid4().hex}"
    from db.models import Company
    from sqlalchemy.orm import Session

    session = Session(bind=local_conn)
    company = Company(name=name, display_name=name)
    session.add(company)
    session.flush()
    company_id = company.id
    session.expunge(company)

    with local_conn.begin_nested():
        local_conn.execute(
            text(
                "UPDATE companies SET "
                "track_record_score = 50, "
                "track_record_json = '{}'::jsonb, "
                "track_record_at = now(), "
                "track_record_version = 'company_track_record_v1' "
                "WHERE id = :id"
            ),
            {"id": company_id},
        )

    assert company_track_record_nonnull_row_count(local_conn) >= 1


def test_apply_readiness_corrupt_when_partially_applied(local_conn):
    """Simulate a corrupt intermediate state: columns exist but one CHECK
    constraint is missing (e.g. a partially-failed prior apply)."""
    _apply_within_savepoint(local_conn)
    with local_conn.begin_nested():
        local_conn.execute(
            text(
                "ALTER TABLE companies DROP CONSTRAINT ck_companies_track_record_score_range"
            )
        )
    readiness = company_track_record_apply_readiness(local_conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any("missing CHECK constraints" in v for v in readiness.violations)
    assert any(
        "ck_companies_track_record_score_range" in v for v in readiness.violations
    )


def test_score_range_check_actually_enforced_after_apply(local_conn):
    """End-to-end sanity: the applied CHECK constraint really works, not
    just that the schema-contract audit believes it exists."""
    _apply_within_savepoint(local_conn)
    name = f"PR-G2A Migration Test {uuid.uuid4().hex}"
    from sqlalchemy.orm import Session

    session = Session(bind=local_conn)
    from db.models import Company

    company = Company(name=name, display_name=name)
    session.add(company)
    session.flush()
    company_id = company.id
    session.expunge(company)

    with pytest.raises(IntegrityError):
        with local_conn.begin_nested():
            local_conn.execute(
                text("UPDATE companies SET track_record_score = 999 WHERE id = :id"),
                {"id": company_id},
            )


# ===================================================================
# 3b. Apply atomicity hardening -- scoped, name+table+type idempotency
#     guards, and the apply-then-verify-postcondition-before-commit
#     contract in apply_company_track_record_migration().
# ===================================================================


def test_idempotency_guard_ignores_same_named_constraint_on_other_table(local_conn):
    """A CHECK constraint sharing one of migration 030's exact constraint
    names, but living on a DIFFERENT table, must not fool the migration's
    `IF NOT EXISTS (... WHERE conname = ...)` guard into skipping creation
    of the real constraint on companies. This is only true because the
    guards also filter on conrelid = 'companies'::regclass AND
    contype = 'c' -- a conname-only guard would false-positive here."""
    with local_conn.begin_nested():
        local_conn.execute(
            text(
                "CREATE TABLE pr_g2a_scratch_conflict ("
                "id serial primary key, val integer, "
                "CONSTRAINT ck_companies_track_record_score_range CHECK (val > 0)"
                ")"
            )
        )

    _apply_within_savepoint(local_conn)

    readiness = company_track_record_apply_readiness(local_conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED
    assert readiness.violations == []

    row = local_conn.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'ck_companies_track_record_score_range' "
            "AND conrelid = 'companies'::regclass AND contype = 'c'"
        )
    ).first()
    assert row is not None, "the real constraint on companies must exist"


def test_apply_and_verify_raises_postcondition_error_on_incomplete_ddl(
    monkeypatch, local_conn
):
    """Artificially incomplete DDL (only 1 of 4 columns, no constraints)
    must fail the post-apply conformance check inside the same
    transaction -- not silently succeed."""
    import db.track_record_migration as mod

    monkeypatch.setattr(
        mod,
        "company_track_record_migration_statements",
        lambda: [
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS track_record_score INTEGER NULL;"
        ],
    )
    with pytest.raises(CompanyTrackRecordApplyPostconditionError) as excinfo:
        with local_conn.begin_nested():
            mod._apply_and_verify_within_transaction(local_conn)
    assert "does not fully conform" in str(excinfo.value)


def test_apply_rolls_back_fully_on_postcondition_failure_against_real_engine(
    monkeypatch,
):
    """Calls apply_company_track_record_migration() directly against the
    real local engine with intentionally-broken DDL -- safe because a
    raised postcondition error triggers engine.begin()'s own automatic
    rollback, so nothing persists. Confirms all four intended column
    additions (not just the one broken statement actually executed) are
    absent afterward -- i.e. the whole migration attempt left zero trace,
    not a partial one."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='companies' "
                "AND column_name = ANY(:names)"
            ),
            {"names": company_track_record_column_names()},
        ).all()
    if existing:
        pytest.skip(
            "companies already has one or more track_record_* columns in this "
            "local DB -- refusing to risk mutating a real pre-existing state"
        )

    import db.track_record_migration as mod

    monkeypatch.setattr(
        mod,
        "company_track_record_migration_statements",
        lambda: [
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS track_record_score INTEGER NULL;"
        ],
    )

    with pytest.raises(CompanyTrackRecordApplyPostconditionError):
        apply_company_track_record_migration(engine)

    with engine.begin() as conn:
        remaining = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='companies' "
                "AND column_name = ANY(:names)"
            ),
            {"names": company_track_record_column_names()},
        ).all()
    assert remaining == [], (
        "postcondition failure must roll back ALL statements executed so far "
        f"(including the one column that was added pre-rollback), got: {remaining}"
    )
    engine.dispose()


def test_apply_and_verify_confirms_contract_and_empty_state_before_returning(
    local_conn,
):
    """The success path: apply_company_track_record_migration()'s shared
    core must confirm both postconditions (full conformance, zero
    non-NULL rows) -- read through the same connection the DDL ran on --
    before returning success. Exercised via the shared core function
    against a rolled-back savepoint (never a real commit) since a
    genuinely successful call to the public apply_company_track_record_migration()
    would commit for real against the local database."""
    import db.track_record_migration as mod

    with local_conn.begin_nested():
        result = mod._apply_and_verify_within_transaction(local_conn)

    assert result["conforms"] is True
    assert result["nonnull_row_count"] == 0
    assert result["statements_executed"] == 5
    assert result["migration"] == "030_company_track_record"

    # And the real schema-contract check, read through this same
    # connection, independently agrees.
    readiness = company_track_record_apply_readiness(local_conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED


def _write_one_nonnull_row(conn) -> None:
    from db.models import Company
    from sqlalchemy.orm import Session

    name = f"PR-G2A Audit Report Test {uuid.uuid4().hex}"
    session = Session(bind=conn)
    company = Company(name=name, display_name=name)
    session.add(company)
    session.flush()
    company_id = company.id
    session.expunge(company)

    with conn.begin_nested():
        conn.execute(
            text(
                "UPDATE companies SET "
                "track_record_score = 42, "
                "track_record_json = '{}'::jsonb, "
                "track_record_at = now(), "
                "track_record_version = 'company_track_record_v1' "
                "WHERE id = :id"
            ),
            {"id": company_id},
        )


def test_build_audit_report_not_applied_fails_by_default(local_conn):
    report = build_track_record_audit_report(local_conn)
    assert report["status"] == "FAIL"
    assert report["columns_exist"] is False
    assert report["require_empty"] is False
    assert report["nonnull_row_count"] is None


def test_build_audit_report_default_passes_when_conforms_and_empty(local_conn):
    _apply_within_savepoint(local_conn)
    report = build_track_record_audit_report(local_conn)
    assert report["status"] == "PASS"
    assert report["conforms"] is True
    assert report["nonnull_row_count"] == 0


def test_build_audit_report_default_does_not_fail_on_legitimate_nonnull_rows(
    local_conn,
):
    """Item #5's core requirement: after a (hypothetical future) wiring PR
    has written real data, a non-empty track_record_* column set must not
    by itself fail the default audit -- only --require-empty treats it as
    a violation."""
    _apply_within_savepoint(local_conn)
    _write_one_nonnull_row(local_conn)
    report = build_track_record_audit_report(local_conn, require_empty=False)
    assert report["status"] == "PASS"
    assert report["nonnull_row_count"] >= 1
    assert report["findings"] == []


def test_build_audit_report_require_empty_fails_on_nonnull_rows(local_conn):
    _apply_within_savepoint(local_conn)
    _write_one_nonnull_row(local_conn)
    report = build_track_record_audit_report(local_conn, require_empty=True)
    assert report["status"] == "FAIL"
    assert report["require_empty"] is True
    assert any("--require-empty" in f for f in report["findings"])


def test_build_audit_report_require_empty_passes_when_empty(local_conn):
    """The post-migration/pre-wiring gate: fresh apply, nothing written
    yet -- --require-empty must pass."""
    _apply_within_savepoint(local_conn)
    report = build_track_record_audit_report(local_conn, require_empty=True)
    assert report["status"] == "PASS"
    assert report["nonnull_row_count"] == 0


def test_build_audit_report_reads_schema_and_count_through_same_connection(local_conn):
    """Both halves of the report (schema conformance and the non-NULL row
    count) must observe this connection's own still-uncommitted apply --
    proving they are read through one connection/transaction, not two
    independent snapshots that could disagree."""
    _apply_within_savepoint(local_conn)
    _write_one_nonnull_row(local_conn)
    report = build_track_record_audit_report(local_conn)
    assert report["columns_exist"] is True
    assert report["conforms"] is True
    assert report["nonnull_row_count"] >= 1


# ===================================================================
# 4. CLI script structural contract -- no persistent DB mutation.
#    --apply is only exercised on fail-fast, pre-DDL refusal paths.
# ===================================================================


def test_migration_module_does_not_import_init_db():
    import ast

    tree = ast.parse(MIGRATION_SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "init_db" not in {alias.name for alias in node.names}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "init_db"


def test_migration_script_not_wired_into_run_migrations():
    connection_source = (ROOT / "db" / "connection.py").read_text(encoding="utf-8")
    assert "track_record" not in connection_source


def test_argparse_rejects_combining_dry_run_and_apply():
    result = subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), "--dry-run", "--apply"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "only one of" in result.stderr.lower()


def test_artifact_path_flag_requires_apply():
    result = subprocess.run(
        [
            sys.executable,
            str(MIGRATION_SCRIPT),
            "--dry-run",
            "--artifact-path",
            "unused.json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "--artifact-path" in result.stderr
    assert "--apply" in result.stderr


def test_dry_run_writes_artifact_and_never_touches_schema(tmp_path):
    """--dry-run is Class A (read-only) -- safe to exercise via subprocess
    against local Postgres without violating the no-persistent-apply rule."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    artifact_path = tmp_path / "dryrun.json"
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), "--dry-run", str(artifact_path)],
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
    assert payload["migration"] == "030_company_track_record"
    assert "not_wired_to" in payload

    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='companies' "
                "AND column_name = 'track_record_score'"
            )
        ).first()
    assert exists is None, "dry-run must never apply schema DDL"
    engine.dispose()


def test_apply_refuses_without_any_dry_run_artifact(tmp_path):
    """--apply with a missing artifact must fail fast, before touching the
    database at all -- safe to exercise via subprocess since the DDL path
    is never reached."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    missing_artifact = tmp_path / "does_not_exist.json"
    result = subprocess.run(
        [
            sys.executable,
            str(MIGRATION_SCRIPT),
            "--apply",
            "--artifact-path",
            str(missing_artifact),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "stale" in result.stderr.lower()
    assert "missing artifact" in result.stderr.lower()

    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='companies' "
                "AND column_name = 'track_record_score'"
            )
        ).first()
    assert exists is None, "a refused apply must not have created any column"
    engine.dispose()


def test_audit_script_module_does_not_import_init_db():
    import ast

    tree = ast.parse(AUDIT_SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "init_db" not in {alias.name for alias in node.names}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "init_db"


def test_audit_script_has_no_allow_production_flag():
    """Class A contract: the audit script must never accept a destructive
    confirmation flag. Checked structurally (no add_argument call, no
    add_production_safety_args wiring) rather than a raw substring search
    -- the script's own docstring explains, in prose, that it has no
    --allow-production flag, which would false-positive a plain
    'not in source' check."""
    source = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "add_production_safety_args" not in source
    assert 'add_argument(\n        "--allow-production"' not in source
    assert 'add_argument("--allow-production"' not in source


def test_audit_runs_readonly_locally_and_reports_not_applied(tmp_path):
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)
    assert payload["migration"] == "030_company_track_record"
    assert payload["migration_sql_touches_only_companies"] is True
    # Local DB has not had migration 030 applied by anything in this run.
    assert payload["columns_exist"] is False
    assert payload["status"] == "FAIL"
    assert payload["require_empty"] is False
    assert result.returncode == 1
    engine.dispose()


def test_audit_script_calls_get_engine_exactly_once_and_never_a_session():
    """Item #4's single-Engine/single-connection contract, checked
    structurally: the script must call get_engine() exactly once and must
    never construct a Session or call get_session_factory() -- either of
    those would open a second, independent snapshot of the database."""
    source = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert source.count("get_engine(") == 1
    assert "get_session_factory" not in source
    assert "Session(" not in source
    assert "sessionmaker" not in source


def test_audit_script_uses_a_single_connection_variable():
    """Structural: exactly one engine.connect() call -- no second
    connection opened anywhere in the script."""
    source = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert source.count(".connect()") == 1


def test_audit_script_sets_repeatable_read_read_only():
    source = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in source


def test_audit_script_rolls_back_never_commits():
    """The audit's own transaction is always rolled back, never committed
    -- read-only in fact, not just in intent."""
    source = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "trans.rollback()" in source
    assert "trans.commit()" not in source
    assert ".commit()" not in source


def test_audit_script_has_require_empty_flag():
    source = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert '"--require-empty"' in source
