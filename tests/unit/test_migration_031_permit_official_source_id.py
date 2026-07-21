"""Static, no-database contract tests for migration 031 (Surrey official
source identity schema foundation, PR-EN1E-1)."""

from __future__ import annotations

from db.permit_official_source_id_ddl import (
    permit_official_source_id_migration_column_names,
    permit_official_source_id_migration_statements,
    permit_official_source_id_migration_touches_only_permits,
    permit_official_source_id_rollback_statements,
)


def test_forward_migration_has_exactly_two_statements():
    statements = permit_official_source_id_migration_statements()
    assert len(statements) == 2


def test_forward_migration_adds_one_nullable_column():
    statements = permit_official_source_id_migration_statements()
    add_column = next(s for s in statements if "ADD COLUMN" in s)
    assert "IF NOT EXISTS official_source_id" in add_column
    assert "NULL" in add_column
    assert "NOT NULL" not in add_column


def test_forward_migration_creates_a_partial_unique_index():
    statements = permit_official_source_id_migration_statements()
    index_stmt = next(s for s in statements if "INDEX" in s)
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in index_stmt
    assert "ux_permits_source_official_source_id" in index_stmt
    assert "ON permits (source, official_source_id)" in index_stmt
    assert (
        "WHERE official_source_id IS NOT NULL AND official_source_id <> ''"
        in index_stmt
    )


def test_forward_migration_is_idempotent_by_construction():
    """Every DDL statement uses an IF NOT EXISTS guard -- safe to re-run."""
    for statement in permit_official_source_id_migration_statements():
        assert "IF NOT EXISTS" in statement


def test_forward_migration_never_mentions_external_id():
    """external_id must never be altered, renamed, or reinterpreted here."""
    for statement in permit_official_source_id_migration_statements():
        assert "external_id" not in statement


def test_forward_migration_touches_only_permits_table():
    ok, offenders = permit_official_source_id_migration_touches_only_permits()
    assert ok, offenders


def test_rollback_has_exactly_two_statements():
    statements = permit_official_source_id_rollback_statements()
    assert len(statements) == 2


def test_rollback_drops_the_index_before_the_column():
    statements = permit_official_source_id_rollback_statements()
    assert "DROP INDEX IF EXISTS ux_permits_source_official_source_id" in statements[0]
    assert "DROP COLUMN IF EXISTS official_source_id" in statements[1]


def test_rollback_is_idempotent_by_construction():
    for statement in permit_official_source_id_rollback_statements():
        assert "IF EXISTS" in statement


def test_rollback_never_mentions_external_id():
    for statement in permit_official_source_id_rollback_statements():
        assert "external_id" not in statement


def test_column_names_is_exactly_official_source_id():
    assert permit_official_source_id_migration_column_names() == ["official_source_id"]
