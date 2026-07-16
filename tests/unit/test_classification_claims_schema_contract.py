"""DB-backed tests for db.classification_claims_schema_contract.verify_schema_contract().

Local Postgres only — skipped when unavailable or when DATABASE_URL resolves
to production. Each test applies the real migration 029 schema, then damages
exactly one expected object (column, CHECK, index, unique index, FK, or PK)
via raw ALTER/DROP, and asserts the contract verifier detects exactly that
violation and reports non-conformance — proving the audit and the migration's
own "already applied" gate cannot be fooled by a partially-correct schema.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from db.classification_claims_ddl import classification_claims_table_names
from db.classification_claims_migration import apply_classification_claims_migration
from db.classification_claims_schema_contract import verify_schema_contract


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing schema contract tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def claims_engine():
    import config.env  # noqa: F401

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
    try:
        yield engine
    finally:
        _drop_all()
        engine.dispose()


@pytest.fixture()
def claims_schema(claims_engine):
    apply_classification_claims_migration(claims_engine)
    return claims_engine


def _drop_foreign_key_on_column(engine, table: str, column: str) -> None:
    with engine.begin() as conn:
        conname = conn.execute(
            text("""
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_attribute att
                    ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
                WHERE con.conrelid = (:t)::regclass AND con.contype = 'f' AND att.attname = :c
                """),
            {"t": table, "c": column},
        ).scalar_one()
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {conname}"))


def _drop_primary_key(engine, table: str) -> None:
    # CASCADE: rule_set_versions.rule_set_version_id is referenced by FKs on
    # four other tables — this test only asserts the PK-loss is detected on
    # the damaged table itself, not that the other tables stay untouched.
    with engine.begin() as conn:
        conname = conn.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid = (:t)::regclass AND contype = 'p'"
            ),
            {"t": table},
        ).scalar_one()
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {conname} CASCADE"))


def _replace_check_constraint(
    engine, table: str, name: str, new_condition_sql: str
) -> None:
    """Drop the named CHECK and recreate it with the SAME name but a
    different condition — proves conformance is not fooled by a matching
    constraint name alone."""
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {name}"))
        conn.execute(
            text(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({new_condition_sql})"
            )
        )


def _replace_index(engine, table: str, name: str, create_index_sql: str) -> None:
    """Drop the named index and recreate it with the SAME name via the given
    full CREATE [UNIQUE] INDEX statement — proves conformance is not fooled
    by a matching index name alone."""
    with engine.begin() as conn:
        conn.execute(text(f"DROP INDEX {name}"))
        conn.execute(text(create_index_sql))


def _replace_foreign_key(
    engine,
    table: str,
    column: str,
    new_referenced_table: str,
    new_referenced_column: str,
) -> None:
    """Drop the FK on `column` and recreate it under the SAME constraint name
    but pointing at a different (still type-compatible, constructible)
    target — proves conformance is not fooled by the source column alone
    when the referenced table/column is wrong."""
    with engine.begin() as conn:
        conname = conn.execute(
            text("""
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_attribute att
                    ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
                WHERE con.conrelid = (:t)::regclass AND con.contype = 'f' AND att.attname = :c
                """),
            {"t": table, "c": column},
        ).scalar_one()
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {conname}"))
        conn.execute(
            text(
                f"ALTER TABLE {table} ADD CONSTRAINT {conname} FOREIGN KEY ({column}) "
                f"REFERENCES {new_referenced_table} ({new_referenced_column})"
            )
        )


# --- positive baseline --------------------------------------------------------------


def test_verify_schema_contract_fully_conforms_after_apply(claims_schema):
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert result.all_tables_exist
    assert result.fully_conforms
    assert result.describe_violations() == []


def test_verify_schema_contract_reports_no_tables_before_apply(claims_engine):
    with claims_engine.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.any_table_exists
    assert not result.fully_conforms
    for tc in result.tables.values():
        assert not tc.exists
    assert all("does not exist" in line for line in result.describe_violations())


# --- negative matrix: exactly one missing object each ------------------------------


def test_verify_schema_contract_detects_missing_column(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(text("ALTER TABLE rule_set_versions DROP COLUMN description"))
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert "description" in result.tables["rule_set_versions"].missing_columns


def test_verify_schema_contract_detects_wrong_column_type(claims_schema):
    # rule_set_versions.description has no CHECK constraint tied to its
    # type, so TEXT -> VARCHAR(50) is a clean cast with no operator conflict.
    with claims_schema.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE rule_set_versions ALTER COLUMN description TYPE VARCHAR(50) "
                "USING description::VARCHAR(50)"
            )
        )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert "description" in result.tables["rule_set_versions"].wrong_type_columns


def test_verify_schema_contract_detects_wrong_nullability(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(
            text("ALTER TABLE claim_events ALTER COLUMN rationale SET NOT NULL")
        )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert "rationale" in result.tables["claim_events"].wrong_nullability_columns


def test_verify_schema_contract_detects_missing_primary_key(claims_schema):
    _drop_primary_key(claims_schema, "rule_set_versions")
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert result.tables["rule_set_versions"].missing_primary_key


def test_verify_schema_contract_detects_missing_unique_constraint(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE classification_claims DROP CONSTRAINT uq_classification_claims_idempotency"
            )
        )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert ("idempotency_key",) in result.tables[
        "classification_claims"
    ].missing_unique_constraints


def test_verify_schema_contract_detects_missing_check_constraint(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE classification_claims DROP CONSTRAINT ck_claim_type_predicate"
            )
        )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert (
        "ck_claim_type_predicate"
        in result.tables["classification_claims"].missing_check_constraints
    )


def test_verify_schema_contract_detects_missing_regular_index(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(text("DROP INDEX ix_claim_evidence_claim"))
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert "ix_claim_evidence_claim" in result.tables["claim_evidence"].missing_indexes


def test_verify_schema_contract_detects_missing_unique_index(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(text("DROP INDEX uq_claim_events_one_per_claim"))
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    assert (
        "uq_claim_events_one_per_claim" in result.tables["claim_events"].missing_indexes
    )


def test_verify_schema_contract_detects_missing_foreign_key(claims_schema):
    _drop_foreign_key_on_column(claims_schema, "classification_claims", "company_id")
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    missing_cols = {
        fk.source_column
        for fk in result.tables["classification_claims"].missing_foreign_keys
    }
    assert "company_id" in missing_cols


# --- negative matrix: matching NAME but wrong DEFINITION (not caught by name-only checks) --


def test_verify_schema_contract_detects_check_constraint_with_wrong_expression(
    claims_schema,
):
    """Same constraint name, different (weaker) condition. A name-only check
    would report this as conformant -- verify_schema_contract must not."""
    _replace_check_constraint(
        claims_schema,
        "classification_claims",
        "ck_claim_type_predicate",
        "claim_type IS NOT NULL",
    )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    tc = result.tables["classification_claims"]
    assert "ck_claim_type_predicate" in tc.wrong_check_constraints
    assert "ck_claim_type_predicate" not in tc.missing_check_constraints


def test_verify_schema_contract_detects_regular_index_on_wrong_column(claims_schema):
    """Same index name, but built on a different column than expected."""
    _replace_index(
        claims_schema,
        "claim_evidence",
        "ix_claim_evidence_claim",
        "CREATE INDEX ix_claim_evidence_claim ON claim_evidence (created_at)",
    )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    tc = result.tables["claim_evidence"]
    assert "ix_claim_evidence_claim" in tc.wrong_indexes
    assert "ix_claim_evidence_claim" not in tc.missing_indexes


def test_verify_schema_contract_detects_one_per_claim_index_no_longer_unique(
    claims_schema,
):
    """uq_claim_events_one_per_claim must be UNIQUE specifically on claim_id
    -- a same-named but non-unique index on the same column must fail."""
    _replace_index(
        claims_schema,
        "claim_events",
        "uq_claim_events_one_per_claim",
        "CREATE INDEX uq_claim_events_one_per_claim ON claim_events (claim_id)",
    )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    tc = result.tables["claim_events"]
    assert "uq_claim_events_one_per_claim" in tc.wrong_indexes
    assert "uq_claim_events_one_per_claim" not in tc.missing_indexes


def test_verify_schema_contract_detects_foreign_key_with_wrong_target(claims_schema):
    """Same source column (claim_evidence.claim_id), FK still present and
    still valid to construct, but pointing at a different (wrong) target
    table/column than the contract requires."""
    _replace_foreign_key(
        claims_schema, "claim_evidence", "claim_id", "claim_events", "event_id"
    )
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.fully_conforms
    tc = result.tables["claim_evidence"]
    missing_targets = {
        (fk.source_column, fk.referenced_table, fk.referenced_column)
        for fk in tc.missing_foreign_keys
    }
    assert ("claim_id", "classification_claims", "claim_id") in missing_targets


def test_verify_schema_contract_isolates_the_single_damaged_table(claims_schema):
    """Damaging one table must not report false violations on the other five."""
    with claims_schema.begin() as conn:
        conn.execute(text("DROP INDEX ix_claim_evidence_claim"))
    with claims_schema.connect() as conn:
        result = verify_schema_contract(conn)
    assert not result.tables["claim_evidence"].conforms
    for name, tc in result.tables.items():
        if name != "claim_evidence":
            assert tc.conforms, f"{name} unexpectedly reported non-conformant"
