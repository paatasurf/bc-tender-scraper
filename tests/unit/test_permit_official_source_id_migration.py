"""Unit and local-Postgres regression tests for
db/permit_official_source_id_migration.py (migration 031 apply-readiness
and transactional apply, PR-EN1E-1)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from db.permit_official_source_id_ddl import (
    is_valid_ddl_digest,
    permit_official_source_id_ddl_digest,
)
from db.permit_official_source_id_migration import (
    ApplyReadinessStatus,
    PermitOfficialSourceIdApplyPostconditionError,
    apply_permit_official_source_id_migration,
    permit_official_source_id_apply_readiness,
    permit_official_source_id_before_stats,
    permit_official_source_id_nonnull_row_count,
)
from tests.db_test_safety import require_local_test_database

_CONFORMING_COLUMN = {
    "data_type": "character varying",
    "character_maximum_length": 100,
    "is_nullable": "YES",
    "column_default": None,
}

# Postgres's real pg_get_expr() deparsed form -- casts and grouping parens
# included, exactly as the live catalog would return it.
_CONFORMING_PREDICATE = (
    "((official_source_id IS NOT NULL) AND " "((official_source_id)::text <> ''::text))"
)
_CONFORMING_INDEX = {
    "is_unique": True,
    "predicate": _CONFORMING_PREDICATE,
    "key_columns": ["source", "official_source_id"],
}


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar_one(self):
        return self._row


class _FakeConn:
    def __init__(self, *, column=None, index=None, nonnull_count=0):
        self._column = column
        self._index = index
        self._nonnull_count = nonnull_count
        self.executed: list[str] = []

    def execute(self, statement, _params=None):
        sql = str(statement)
        self.executed.append(sql)
        if "information_schema.columns" in sql:
            return _Result(self._column)
        if "pg_index" in sql:
            return _Result(self._index)
        if "COUNT(*)" in sql:
            return _Result(self._nonnull_count)
        raise AssertionError(f"unexpected statement: {sql}")


# --- ddl digest -------------------------------------------------------


def test_ddl_digest_is_deterministic():
    assert (
        permit_official_source_id_ddl_digest() == permit_official_source_id_ddl_digest()
    )


def test_ddl_digest_is_valid_sha256():
    assert is_valid_ddl_digest(permit_official_source_id_ddl_digest())


@pytest.mark.parametrize("bad", [None, "", "not-hex", "A" * 64, "0" * 63, 12345])
def test_is_valid_ddl_digest_rejects_malformed(bad):
    assert is_valid_ddl_digest(bad) is False


# --- apply readiness (pure logic, fake conn) ---------------------------


def test_readiness_not_applied_when_neither_column_nor_index_exist():
    conn = _FakeConn(column=None, index=None)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.NOT_APPLIED
    assert readiness.violations == []


def test_readiness_fully_applied_when_both_conform():
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=_CONFORMING_INDEX)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED
    assert readiness.violations == []


def test_readiness_corrupt_when_column_exists_without_index():
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=None)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any("index is missing" in v for v in readiness.violations)


def test_readiness_corrupt_when_index_exists_without_column():
    conn = _FakeConn(column=None, index=_CONFORMING_INDEX)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any("column is missing" in v for v in readiness.violations)


@pytest.mark.parametrize(
    "bad_column",
    [
        {**_CONFORMING_COLUMN, "data_type": "text"},
        {**_CONFORMING_COLUMN, "character_maximum_length": 300},
        {**_CONFORMING_COLUMN, "is_nullable": "NO"},
        {**_CONFORMING_COLUMN, "column_default": "''::character varying"},
    ],
)
def test_readiness_corrupt_for_each_column_violation(bad_column):
    conn = _FakeConn(column=bad_column, index=_CONFORMING_INDEX)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert readiness.violations


# --- index catalog-based conformance: the exact cases the task asked for --


def test_readiness_corrupt_when_index_is_not_unique():
    non_unique = {**_CONFORMING_INDEX, "is_unique": False}
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=non_unique)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


def test_readiness_corrupt_when_key_order_is_reversed():
    wrong_order = {**_CONFORMING_INDEX, "key_columns": ["official_source_id", "source"]}
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=wrong_order)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


def test_readiness_corrupt_when_key_columns_differ():
    wrong_keys = {**_CONFORMING_INDEX, "key_columns": ["source", "external_id"]}
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=wrong_keys)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


def test_readiness_corrupt_when_index_has_no_predicate():
    no_predicate = {**_CONFORMING_INDEX, "predicate": None}
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=no_predicate)
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


def test_readiness_corrupt_when_predicate_lacks_is_not_null():
    predicate = "((official_source_id)::text <> ''::text)"
    conn = _FakeConn(
        column=_CONFORMING_COLUMN, index={**_CONFORMING_INDEX, "predicate": predicate}
    )
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


def test_readiness_corrupt_when_predicate_lacks_empty_string_exclusion():
    predicate = "(official_source_id IS NOT NULL)"
    conn = _FakeConn(
        column=_CONFORMING_COLUMN, index={**_CONFORMING_INDEX, "predicate": predicate}
    )
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


@pytest.mark.parametrize(
    "predicate",
    [
        # a third, unrelated conjunct added on
        "((official_source_id IS NOT NULL) AND ((official_source_id)::text <> ''::text) "
        "AND (source = 'surrey'::text))",
        # right shape, wrong column
        "((source IS NOT NULL) AND ((source)::text <> ''::text))",
        # weakened to a different comparison
        "((official_source_id IS NOT NULL) AND ((official_source_id)::text IS DISTINCT FROM ''::text))",
    ],
)
def test_readiness_corrupt_for_other_or_weakened_predicates(predicate):
    conn = _FakeConn(
        column=_CONFORMING_COLUMN, index={**_CONFORMING_INDEX, "predicate": predicate}
    )
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.CORRUPT


def test_readiness_fully_applied_is_insensitive_to_predicate_casing_and_whitespace():
    """The comparison must tolerate Postgres's own deparse formatting
    (casts, parens, spacing) without weakening what it actually accepts.
    (Postgres's ruleutils deparser never inserts whitespace around ``::``
    itself, so that specific token is not varied here -- only the
    surrounding spacing and identifier/keyword casing are.)"""
    predicate = (
        "(  ( OFFICIAL_SOURCE_ID   IS   NOT   NULL )  AND  "
        "( (official_source_id)::text   <>   ''::text )  )"
    )
    conn = _FakeConn(
        column=_CONFORMING_COLUMN, index={**_CONFORMING_INDEX, "predicate": predicate}
    )
    readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED


def test_before_stats_shows_pending_before_apply():
    conn = _FakeConn(column=None, index=None)
    stats = permit_official_source_id_before_stats(conn)
    assert stats["migration_pending"] is True
    assert stats["column_exists"] is False
    assert stats["index_exists"] is False


def test_before_stats_shows_not_pending_after_apply():
    conn = _FakeConn(column=_CONFORMING_COLUMN, index=_CONFORMING_INDEX)
    stats = permit_official_source_id_before_stats(conn)
    assert stats["migration_pending"] is False


def test_nonnull_row_count_zero_when_column_does_not_exist_yet():
    conn = _FakeConn(column=None, index=None)
    assert permit_official_source_id_nonnull_row_count(conn) == 0


def test_nonnull_row_count_reads_real_count_when_column_exists():
    conn = _FakeConn(
        column=_CONFORMING_COLUMN, index=_CONFORMING_INDEX, nonnull_count=3
    )
    assert permit_official_source_id_nonnull_row_count(conn) == 3


# --- transactional apply (local Postgres) ------------------------------


@pytest.fixture()
def local_engine():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DROP INDEX IF EXISTS ux_permits_source_official_source_id")
            )
            conn.execute(
                text("ALTER TABLE permits DROP COLUMN IF EXISTS official_source_id")
            )
        engine.dispose()


def test_apply_is_idempotent_when_run_twice(local_engine):
    first = apply_permit_official_source_id_migration(local_engine)
    assert first["conforms"] is True
    second = apply_permit_official_source_id_migration(local_engine)
    assert second["conforms"] is True

    with local_engine.connect() as conn:
        readiness = permit_official_source_id_apply_readiness(conn)
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED


def test_apply_rolls_back_fully_on_postcondition_failure(local_engine, monkeypatch):
    import db.permit_official_source_id_migration as migration_module

    monkeypatch.setattr(
        migration_module,
        "permit_official_source_id_nonnull_row_count",
        lambda _conn: 1,
    )
    with pytest.raises(PermitOfficialSourceIdApplyPostconditionError):
        apply_permit_official_source_id_migration(local_engine)

    with local_engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'permits' AND column_name = 'official_source_id'"
            )
        ).first()
    assert exists is None
