"""Lightweight checks on migration 031's statement list -- no DB needed.
Mirrors the style of other *_ddl.py test coverage in this repo (e.g. the
registry_gateway_ddl equivalents): confirm the file parses into
well-formed, non-empty SQL statements, nothing more.
"""

from __future__ import annotations

from db.integration_connections_ddl import integration_connections_migration_statements


def test_statements_list_is_non_empty():
    statements = integration_connections_migration_statements()
    assert len(statements) >= 4  # CREATE TABLE + unique index + 2 plain indexes


def test_every_statement_is_terminated_and_non_trivial():
    for statement in integration_connections_migration_statements():
        assert statement.strip().endswith(";")
        assert len(statement.strip()) > 10


def test_create_table_statement_present_and_mentions_expected_columns():
    statements = integration_connections_migration_statements()
    create_stmt = next(
        s for s in statements if s.upper().startswith("CREATE TABLE")
    )
    for column in (
        "company_id",
        "provider",
        "nango_environment",
        "nango_provider_config_key",
        "nango_connection_id",
        "external_org_id",
        "allowed_projects",
        "status",
    ):
        assert column in create_stmt


def test_allowed_projects_array_type_check_constraint_present():
    statements = integration_connections_migration_statements()
    create_stmt = next(s for s in statements if s.upper().startswith("CREATE TABLE"))
    assert "ck_integration_connections_allowed_projects_is_array" in create_stmt
    assert "jsonb_typeof(allowed_projects) = 'array'" in create_stmt


def test_no_statement_touches_any_other_table():
    """This PR creates exactly one new table. Guards against an
    accidental ALTER TABLE on actions/action_approvals/companies/etc.
    sneaking into this migration file."""
    statements = integration_connections_migration_statements()
    for statement in statements:
        upper = statement.upper()
        assert "ALTER TABLE" not in upper
        if "CREATE TABLE" in upper:
            assert "integration_connections" in statement
