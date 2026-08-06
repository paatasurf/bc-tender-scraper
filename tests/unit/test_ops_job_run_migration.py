"""Tests for the M3B ops job run schema foundation: DDL/digest helpers
(db/ops_job_run_ddl.py), schema-contract + apply logic
(db/ops_job_run_migration.py), the CLI runner's dry-run-artifact staleness
gate (scripts/run_ops_job_run_migration.py), and proof that
db.connection.init_db() can never auto-create this schema.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from db.ops_job_run_ddl import (
    is_valid_ddl_digest,
    ops_job_run_ddl_digest,
    ops_job_run_migration_statements,
    ops_job_run_rollback_statements,
)
from db.ops_job_run_migration import (
    ApplyReadinessStatus,
    OpsJobRunApplyPostconditionError,
    apply_ops_job_run_migration,
    ops_job_run_apply_readiness,
    ops_job_run_before_stats,
    ops_job_run_row_counts,
)
from tests.db_test_safety import require_local_test_database

import scripts.run_ops_job_run_migration as runner

# ---------------------------------------------------------------------
# Pure: DDL parsing / digest (no DB)
# ---------------------------------------------------------------------


def test_migration_statements_are_nonempty_and_additive_only():
    statements = ops_job_run_migration_statements()
    assert len(statements) >= 2
    for statement in statements:
        upper = statement.upper()
        assert "DROP TABLE" not in upper
        assert "DELETE FROM" not in upper
        assert (
            "ALTER TABLE" not in upper or "IF NOT EXISTS" not in upper
        )  # no ALTERs at all expected
    assert any("CREATE TABLE IF NOT EXISTS ops_job_runs" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS ops_job_run_events" in s for s in statements)


def test_rollback_statements_drop_both_tables():
    statements = ops_job_run_rollback_statements()
    joined = " ".join(statements).lower()
    assert "drop table if exists ops_job_run_events" in joined
    assert "drop table if exists ops_job_runs" in joined


def test_ddl_digest_is_deterministic_and_a_valid_sha256():
    digest_a = ops_job_run_ddl_digest()
    digest_b = ops_job_run_ddl_digest()
    assert digest_a == digest_b
    assert is_valid_ddl_digest(digest_a)


def test_is_valid_ddl_digest_rejects_malformed_values():
    assert is_valid_ddl_digest(None) is False
    assert is_valid_ddl_digest("") is False
    assert is_valid_ddl_digest("not-hex") is False
    assert is_valid_ddl_digest("a" * 63) is False  # too short


# ---------------------------------------------------------------------
# Pure: init_db() can never auto-create this schema
# ---------------------------------------------------------------------


def test_ops_job_run_tables_are_not_on_db_models_base_metadata():
    from db.models import Base

    assert "ops_job_runs" not in Base.metadata.tables
    assert "ops_job_run_events" not in Base.metadata.tables


def test_ops_job_run_tables_use_a_private_metadata_object():
    from db.models import Base
    from db.ops_job_run_tables import ops_job_run_metadata

    assert ops_job_run_metadata is not Base.metadata


# ---------------------------------------------------------------------
# Real local-Postgres: schema-contract + apply/postcondition logic
# ---------------------------------------------------------------------


@pytest.fixture
def clean_engine():
    """Real local Postgres, with the ops_job_run schema guaranteed ABSENT
    at both start and end of the test."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in ops_job_run_rollback_statements():
            conn.execute(text(statement))

    try:
        yield engine
    finally:
        with engine.begin() as conn:
            for statement in ops_job_run_rollback_statements():
                conn.execute(text(statement))
        engine.dispose()


def test_before_stats_reports_migration_pending_when_absent(clean_engine):
    with clean_engine.connect() as conn:
        stats = ops_job_run_before_stats(conn)
    assert stats["runs_table_exists"] is False
    assert stats["events_table_exists"] is False
    assert stats["migration_pending"] is True


def test_apply_readiness_not_applied_when_schema_absent(clean_engine):
    with clean_engine.connect() as conn:
        readiness = ops_job_run_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.NOT_APPLIED
    assert readiness.violations == ()


def test_apply_creates_a_fully_conforming_empty_schema(clean_engine):
    result = apply_ops_job_run_migration(clean_engine)
    assert result["conforms"] is True
    assert result["row_counts"] == {"runs": 0, "events": 0}

    with clean_engine.connect() as conn:
        readiness = ops_job_run_apply_readiness(conn)
        assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED
        assert readiness.violations == ()

        stats = ops_job_run_before_stats(conn)
        assert stats["migration_pending"] is False

        counts = ops_job_run_row_counts(conn)
        assert counts == {"runs": 0, "events": 0}


def test_apply_is_a_safe_noop_check_when_already_fully_applied(clean_engine):
    apply_ops_job_run_migration(clean_engine)
    with clean_engine.connect() as conn:
        readiness = ops_job_run_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED
    # The CLI runner consults exactly this readiness value to decide
    # "Already applied -- nothing to do" without re-running DDL.


def test_apply_readiness_detects_missing_index_as_corrupt(clean_engine):
    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(text("DROP INDEX ux_ops_job_runs_job_type_idempotency_key"))

    with clean_engine.connect() as conn:
        readiness = ops_job_run_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any(
        "ux_ops_job_runs_job_type_idempotency_key" in v for v in readiness.violations
    )


def test_apply_readiness_detects_missing_column_as_corrupt(clean_engine):
    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(text("ALTER TABLE ops_job_runs DROP COLUMN source"))

    with clean_engine.connect() as conn:
        readiness = ops_job_run_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any("ops_job_runs columns do not match" in v for v in readiness.violations)


@pytest.mark.parametrize(
    "constraint_name,table",
    [
        ("ck_ops_job_runs_trigger", "ops_job_runs"),
        ("ck_ops_job_runs_status", "ops_job_runs"),
        ("ck_ops_job_run_events_event_type", "ops_job_run_events"),
    ],
)
def test_apply_readiness_detects_missing_check_constraint_as_corrupt(
    clean_engine, constraint_name, table
):
    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint_name}"))

    with clean_engine.connect() as conn:
        readiness = ops_job_run_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any(constraint_name in v for v in readiness.violations)


def test_check_constraint_rejects_invalid_status_at_db_level(clean_engine):
    from sqlalchemy.exc import IntegrityError

    apply_ops_job_run_migration(clean_engine)
    with pytest.raises(IntegrityError):
        with clean_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops_job_runs "
                    "(run_id, job_type, trigger, status, lease_expires_at) "
                    "VALUES ('bad-status', 'ai_scoring', 'scheduler', "
                    "'not-a-real-status', NOW() + INTERVAL '30 minutes')"
                )
            )


def test_check_constraint_rejects_invalid_trigger_at_db_level(clean_engine):
    from sqlalchemy.exc import IntegrityError

    apply_ops_job_run_migration(clean_engine)
    with pytest.raises(IntegrityError):
        with clean_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops_job_runs "
                    "(run_id, job_type, trigger, status, lease_expires_at) "
                    "VALUES ('bad-trigger', 'ai_scoring', 'cron', "
                    "'running', NOW() + INTERVAL '30 minutes')"
                )
            )


def test_check_constraint_rejects_invalid_event_type_at_db_level(clean_engine):
    from sqlalchemy.exc import IntegrityError

    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops_job_runs "
                "(run_id, job_type, trigger, status, lease_expires_at) "
                "VALUES ('run-for-bad-event', 'ai_scoring', 'scheduler', "
                "'running', NOW() + INTERVAL '30 minutes')"
            )
        )

    with pytest.raises(IntegrityError):
        with clean_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops_job_run_events (run_id, event_type) "
                    "VALUES ('run-for-bad-event', 'heartbeat')"
                )
            )

    with clean_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ops_job_runs WHERE run_id = 'run-for-bad-event'")
        )


def test_apply_rolls_back_entirely_if_postcondition_rows_check_fails(
    clean_engine, monkeypatch
):
    """Simulates the defensive "found existing rows immediately after
    applying" guard -- monkeypatches the row-count check to report a
    non-empty table, and confirms the whole migration (including the
    CREATE TABLE statements) rolls back rather than partially committing."""
    import db.ops_job_run_migration as migration_module

    monkeypatch.setattr(
        migration_module,
        "ops_job_run_row_counts",
        lambda conn: {"runs": 1, "events": 0},
    )

    with pytest.raises(OpsJobRunApplyPostconditionError):
        apply_ops_job_run_migration(clean_engine)

    with clean_engine.connect() as conn:
        stats = ops_job_run_before_stats(conn)
    assert stats["migration_pending"] is True  # rolled back -- schema absent


def test_partial_unique_index_rejects_second_active_idempotency_key(clean_engine):
    """DB-level proof of the idempotency contract: two rows with the same
    (job_type, idempotency_key) are rejected by the database, not just by
    application code."""
    from sqlalchemy.exc import IntegrityError

    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops_job_runs "
                "(run_id, job_type, trigger, status, lease_expires_at, idempotency_key) "
                "VALUES ('run-a', 'surrey_identity_scheduler', 'scheduler', 'running', "
                "NOW() + INTERVAL '30 minutes', 'day-2026-08-06')"
            )
        )

    with pytest.raises(IntegrityError):
        with clean_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops_job_runs "
                    "(run_id, job_type, trigger, status, lease_expires_at, idempotency_key) "
                    "VALUES ('run-b', 'surrey_identity_scheduler', 'scheduler', 'running', "
                    "NOW() + INTERVAL '30 minutes', 'day-2026-08-06')"
                )
            )

    with clean_engine.begin() as conn:
        conn.execute(text("DELETE FROM ops_job_runs"))


def test_two_different_job_types_can_share_the_same_idempotency_key(clean_engine):
    """The partial unique index is scoped to (job_type, idempotency_key)
    together, not idempotency_key alone."""
    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops_job_runs "
                "(run_id, job_type, trigger, status, lease_expires_at, idempotency_key) "
                "VALUES ('run-a', 'surrey_identity_scheduler', 'scheduler', 'running', "
                "NOW() + INTERVAL '30 minutes', 'shared-key')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO ops_job_runs "
                "(run_id, job_type, trigger, status, lease_expires_at, idempotency_key) "
                "VALUES ('run-b', 'ai_scoring', 'scheduler', 'running', "
                "NOW() + INTERVAL '30 minutes', 'shared-key')"
            )
        )
        count = conn.execute(text("SELECT COUNT(*) FROM ops_job_runs")).scalar_one()
    assert count == 2

    with clean_engine.begin() as conn:
        conn.execute(text("DELETE FROM ops_job_runs"))


def test_null_idempotency_key_never_conflicts(clean_engine):
    """Rows with idempotency_key IS NULL are entirely unconstrained by the
    partial unique index."""
    apply_ops_job_run_migration(clean_engine)
    with clean_engine.begin() as conn:
        for run_id in ("run-a", "run-b", "run-c"):
            conn.execute(
                text(
                    "INSERT INTO ops_job_runs "
                    "(run_id, job_type, trigger, status, lease_expires_at, idempotency_key) "
                    "VALUES (:run_id, 'ai_scoring', 'manual', 'running', "
                    "NOW() + INTERVAL '30 minutes', NULL)"
                ),
                {"run_id": run_id},
            )
        count = conn.execute(text("SELECT COUNT(*) FROM ops_job_runs")).scalar_one()
    assert count == 3

    with clean_engine.begin() as conn:
        conn.execute(text("DELETE FROM ops_job_runs"))


# ---------------------------------------------------------------------
# CLI runner: dry-run report shape + apply-time staleness gate
# ---------------------------------------------------------------------


def test_dry_run_report_shape(clean_engine):
    from db.connection import get_session

    session = get_session()
    try:
        report = runner._build_dry_run_report(
            session, artifact_path=runner.DEFAULT_DRY_RUN_ARTIFACT
        )
    finally:
        session.close()

    assert report["class"] == "D"
    assert report["migration"] == "033_ops_job_runs"
    assert report["ddl_digest"] == ops_job_run_ddl_digest()
    assert report["not_wired_to"] == [
        "db.connection._run_migrations()",
        "db.connection.init_db()",
    ]
    assert report["planned_mutations"]["destructive_delete"] is False
    assert report["planned_mutations"]["ddl_only"] is True


def _valid_report(session) -> dict:
    return runner._build_dry_run_report(
        session, artifact_path=runner.DEFAULT_DRY_RUN_ARTIFACT
    )


def test_verify_dry_run_artifact_rejects_missing_file(tmp_path, clean_engine):
    from db.connection import get_session

    session = get_session()
    try:
        with pytest.raises(SystemExit):
            runner._verify_dry_run_artifact(
                session=session, report_path=tmp_path / "missing.json"
            )
    finally:
        session.close()


def test_verify_dry_run_artifact_rejects_malformed_json(tmp_path, clean_engine):
    from db.connection import get_session

    path = tmp_path / "artifact.json"
    path.write_text("{not valid json", encoding="utf-8")

    session = get_session()
    try:
        with pytest.raises(SystemExit):
            runner._verify_dry_run_artifact(session=session, report_path=path)
    finally:
        session.close()


def test_verify_dry_run_artifact_rejects_stale_git_sha(tmp_path, clean_engine):
    from db.connection import get_session

    session = get_session()
    try:
        report = _valid_report(session)
        report["git_commit_sha"] = "0" * 40  # deliberately wrong
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps(report, default=str), encoding="utf-8")

        with pytest.raises(SystemExit):
            runner._verify_dry_run_artifact(session=session, report_path=path)
    finally:
        session.close()


def test_verify_dry_run_artifact_rejects_stale_ddl_digest(tmp_path, clean_engine):
    from db.connection import get_session

    session = get_session()
    try:
        report = _valid_report(session)
        report["ddl_digest"] = "a" * 64  # well-formed but wrong
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps(report, default=str), encoding="utf-8")

        with pytest.raises(SystemExit):
            runner._verify_dry_run_artifact(session=session, report_path=path)
    finally:
        session.close()


def test_verify_dry_run_artifact_rejects_malformed_ddl_digest(tmp_path, clean_engine):
    from db.connection import get_session

    session = get_session()
    try:
        report = _valid_report(session)
        report["ddl_digest"] = "not-a-real-digest"
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps(report, default=str), encoding="utf-8")

        with pytest.raises(SystemExit):
            runner._verify_dry_run_artifact(session=session, report_path=path)
    finally:
        session.close()


def test_verify_dry_run_artifact_rejects_changed_schema_state(tmp_path, clean_engine):
    """A dry-run generated before --apply must be rejected if the schema
    state changed (e.g. someone applied it) between dry-run and apply."""
    from db.connection import get_session

    session = get_session()
    try:
        report = _valid_report(session)  # captured while schema is absent
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps(report, default=str), encoding="utf-8")

        apply_ops_job_run_migration(clean_engine)  # schema state changes

        with pytest.raises(SystemExit):
            runner._verify_dry_run_artifact(session=session, report_path=path)
    finally:
        session.close()


def test_verify_dry_run_artifact_accepts_a_fresh_matching_artifact(
    tmp_path, clean_engine
):
    from db.connection import get_session

    session = get_session()
    try:
        report = _valid_report(session)
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps(report, default=str), encoding="utf-8")

        runner._verify_dry_run_artifact(
            session=session, report_path=path
        )  # must not raise
    finally:
        session.close()
