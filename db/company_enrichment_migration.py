"""Runtime schema-contract checks and apply logic for the company
on-demand enrichment schema (migration 034, RFC Phase 1) -- Class D
helpers.

Applies two brand-new, empty tables (company_enrichment_fields,
company_enrichment_jobs) plus their indexes -- never touches any existing
table (including companies), never writes application data. This module
never applies anything by itself; the only caller is
scripts/run_company_enrichment_migration.py --apply.

No ORM mapping is added by this PR -- pipeline/company_enrichment/* talks
to these tables through plain SQLAlchemy Core Table objects
(db/company_enrichment_tables.py), which are deliberately NOT part of
db.models.Base.metadata, so db.connection.init_db()'s
Base.metadata.create_all() can never auto-create this schema. That wiring
decision is what this migration's runner enforces operationally: the
schema only exists once an operator has explicitly run --apply. Mirrors
db/ops_job_run_migration.py's approach (migration 033) exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from db.company_enrichment_ddl import (
    FIELDS_TABLE,
    JOBS_TABLE,
    company_enrichment_migration_statements,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "CompanyEnrichmentSchemaCorruptError",
    "CompanyEnrichmentApplyPostconditionError",
    "company_enrichment_apply_readiness",
    "company_enrichment_before_stats",
    "company_enrichment_migration_pending",
    "apply_company_enrichment_migration",
    "company_enrichment_row_counts",
]


class CompanyEnrichmentSchemaCorruptError(RuntimeError):
    """Raised when the enrichment tables/indexes exist in a state that
    does not match db/migrations/034_company_enrichment.sql -- e.g. a
    table exists with a missing index, or a differently-shaped partial
    unique index. Fail-closed: the operator must investigate manually.
    Nothing here attempts to silently repair a corrupt schema."""


class CompanyEnrichmentApplyPostconditionError(RuntimeError):
    """Raised by apply_company_enrichment_migration() when, immediately
    after executing migration 034's DDL -- through the exact same
    connection and transaction, before commit -- the resulting schema does
    not fully conform to the expected contract, or either table already
    has rows. Raised inside the same ``with engine.begin()`` block that
    ran the DDL, so it triggers an automatic ROLLBACK of the entire
    migration."""


_FIELDS_EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "company_id",
        "field_name",
        "value",
        "source",
        "confidence",
        "verified",
        "fetched_at",
        "superseded_at",
        "run_id",
    }
)
_JOBS_EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "run_id",
        "company_id",
        "trigger",
        "status",
        "providers_attempted",
        "started_at",
        "finished_at",
        "lease_expires_at",
    }
)


def _table_columns(conn: Any, table: str) -> dict[str, str] | None:
    rows = (
        conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    return {r["column_name"]: r["is_nullable"] for r in rows}


_INDEX_INFO_SQL = text("""
    SELECT
        ix.indisunique AS is_unique,
        pg_get_expr(ix.indpred, ix.indrelid) AS predicate,
        (
            SELECT array_agg(a.attname ORDER BY k.ord)
            FROM unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a
              ON a.attrelid = ix.indrelid AND a.attnum = k.attnum
        ) AS key_columns
    FROM pg_index ix
    JOIN pg_class ic ON ic.oid = ix.indexrelid
    JOIN pg_class tc ON tc.oid = ix.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    WHERE ic.relname = :i AND tc.relname = :t AND n.nspname = 'public'
    """)


def _index_info(conn: Any, *, table: str, index: str) -> dict[str, Any] | None:
    row = conn.execute(_INDEX_INFO_SQL, {"t": table, "i": index}).mappings().first()
    return dict(row) if row is not None else None


_CAST_RE = re.compile(r"::[a-zA-Z_]+(?: [a-zA-Z_]+)*")


def _normalize_predicate(raw: str | None) -> str | None:
    if raw is None:
        return None
    stripped = _CAST_RE.sub("", raw)
    stripped = stripped.replace("(", "").replace(")", "")
    return " ".join(stripped.lower().split())


@dataclass(frozen=True)
class _ExpectedIndex:
    name: str
    table: str
    unique: bool
    key_columns: tuple[str, ...]
    predicate: str | None = None  # normalized, or None for no predicate


_EXPECTED_INDEXES = (
    _ExpectedIndex(
        "ux_company_enrichment_fields_company_field_source",
        FIELDS_TABLE,
        True,
        ("company_id", "field_name", "source"),
        predicate="superseded_at is null",
    ),
    _ExpectedIndex(
        "ix_company_enrichment_fields_company",
        FIELDS_TABLE,
        False,
        ("company_id",),
        predicate="superseded_at is null",
    ),
    _ExpectedIndex("ux_company_enrichment_jobs_run_id", JOBS_TABLE, True, ("run_id",)),
    _ExpectedIndex(
        "ux_company_enrichment_jobs_company_active",
        JOBS_TABLE,
        True,
        ("company_id",),
        predicate="status = 'running'",
    ),
    _ExpectedIndex(
        "ix_company_enrichment_jobs_company_started_at",
        JOBS_TABLE,
        False,
        ("company_id", "started_at"),
    ),
)


def _index_conforms(expected: _ExpectedIndex, actual: dict[str, Any] | None) -> bool:
    if actual is None:
        return False
    if bool(actual.get("is_unique")) != expected.unique:
        return False
    if list(actual.get("key_columns") or []) != list(expected.key_columns):
        return False
    if expected.predicate is None:
        return not actual.get("predicate")
    return _normalize_predicate(actual.get("predicate")) == expected.predicate


def _fk_exists(
    conn: Any, *, table: str, local_column: str, ref_table: str, ref_column: str
) -> bool:
    """True iff `table.local_column` has a FOREIGN KEY to `ref_table.ref_column`.

    Bugbot finding fix: the original query joined table_constraints only
    to constraint_column_usage (which reports the REFERENCED side of a
    FOREIGN KEY constraint), and never consulted key_column_usage (which
    reports the LOCAL/constrained side). It therefore only proved "table
    has SOME foreign key pointing at ref_table.ref_column" -- it could
    never have caught a corrupt schema where an unrelated column carried
    the FK instead of local_column, or where local_column's own FK had
    been dropped while a different, unrelated FK to the same ref_table
    happened to still exist. Joining key_column_usage as well pins down
    the LOCAL column too, matching what the function name always claimed
    to check.
    """
    row = conn.execute(
        text("""
            SELECT 1
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
              AND tc.table_schema = ccu.table_schema
            WHERE tc.table_name = :table
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = :local_column
              AND ccu.table_name = :ref_table
              AND ccu.column_name = :ref_column
            """),
        {
            "table": table,
            "local_column": local_column,
            "ref_table": ref_table,
            "ref_column": ref_column,
        },
    ).first()
    return row is not None


_EXPECTED_CHECK_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (JOBS_TABLE, "ck_company_enrichment_jobs_trigger"),
    (JOBS_TABLE, "ck_company_enrichment_jobs_status"),
)

_CHECK_CONSTRAINT_EXISTS_SQL = text("""
    SELECT 1
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE con.contype = 'c'
      AND con.conname = :name
      AND rel.relname = :table
      AND nsp.nspname = 'public'
    """)


def _check_constraint_exists(conn: Any, *, table: str, name: str) -> bool:
    row = conn.execute(
        _CHECK_CONSTRAINT_EXISTS_SQL, {"table": table, "name": name}
    ).first()
    return row is not None


def company_enrichment_before_stats(session_or_conn: Any) -> dict[str, Any]:
    """Lightweight existence-only snapshot -- used as the dry-run-artifact
    staleness signal. Does NOT verify the full contract -- see
    company_enrichment_apply_readiness() for that."""
    fields_columns = _table_columns(session_or_conn, FIELDS_TABLE)
    jobs_columns = _table_columns(session_or_conn, JOBS_TABLE)
    statements = company_enrichment_migration_statements()
    return {
        "fields_table_exists": fields_columns is not None,
        "jobs_table_exists": jobs_columns is not None,
        "migration_pending": fields_columns is None or jobs_columns is None,
        "statements_planned": len(statements),
    }


def company_enrichment_migration_pending(session_or_conn: Any) -> bool:
    return bool(
        company_enrichment_before_stats(session_or_conn).get("migration_pending")
    )


class ApplyReadinessStatus(str, Enum):
    NOT_APPLIED = "not_applied"  # neither table exists -- safe to apply
    FULLY_APPLIED = "fully_applied"  # both tables + all indexes + FKs conform
    CORRUPT = "corrupt"  # something exists but doesn't fully match the contract


@dataclass(frozen=True)
class ApplyReadiness:
    status: ApplyReadinessStatus
    fields_columns: dict[str, str] | None
    jobs_columns: dict[str, str] | None
    violations: tuple[str, ...]


def company_enrichment_apply_readiness(session_or_conn: Any) -> ApplyReadiness:
    """Full schema-contract check: table + column-set + index (uniqueness,
    key columns, partial predicate) + foreign-key + CHECK-constraint
    conformance -- not just existence. This is what --apply consults
    before deciding whether to report "Already applied," proceed, or fail
    closed as corrupt."""
    fields_columns = _table_columns(session_or_conn, FIELDS_TABLE)
    jobs_columns = _table_columns(session_or_conn, JOBS_TABLE)

    if fields_columns is None and jobs_columns is None:
        return ApplyReadiness(
            status=ApplyReadinessStatus.NOT_APPLIED,
            fields_columns=None,
            jobs_columns=None,
            violations=(),
        )

    violations: list[str] = []

    if fields_columns is None:
        violations.append(f"{FIELDS_TABLE} table is missing")
    elif set(fields_columns) != _FIELDS_EXPECTED_COLUMNS:
        violations.append(
            f"{FIELDS_TABLE} columns do not match: "
            f"missing={sorted(_FIELDS_EXPECTED_COLUMNS - set(fields_columns))} "
            f"unexpected={sorted(set(fields_columns) - _FIELDS_EXPECTED_COLUMNS)}"
        )

    if jobs_columns is None:
        violations.append(f"{JOBS_TABLE} table is missing")
    elif set(jobs_columns) != _JOBS_EXPECTED_COLUMNS:
        violations.append(
            f"{JOBS_TABLE} columns do not match: "
            f"missing={sorted(_JOBS_EXPECTED_COLUMNS - set(jobs_columns))} "
            f"unexpected={sorted(set(jobs_columns) - _JOBS_EXPECTED_COLUMNS)}"
        )

    # Safety review finding #3: index/FK/CHECK checks below are gated on
    # EACH table's own existence independently, not on both tables
    # existing together. A schema in a PARTIAL state (e.g. an earlier
    # failed/interrupted apply left fields_table created with a
    # wrong-shaped index, but jobs_table not created yet) must still
    # have that wrong index surfaced as a violation right now -- an
    # operator reading the dry-run report in that exact partial state
    # must see everything wrong with what already exists, not just "the
    # other table is missing" (which is true but incomplete, and could
    # read as "just create the other table and you're done").
    if fields_columns is not None:
        for expected in _EXPECTED_INDEXES:
            if expected.table != FIELDS_TABLE:
                continue
            actual = _index_info(
                session_or_conn, table=expected.table, index=expected.name
            )
            if not _index_conforms(expected, actual):
                violations.append(f"{expected.name} index does not conform: {actual}")
        if not _fk_exists(
            session_or_conn,
            table=FIELDS_TABLE,
            local_column="company_id",
            ref_table="companies",
            ref_column="id",
        ):
            violations.append(
                f"{FIELDS_TABLE}.company_id -> companies.id foreign key is missing"
            )

    if jobs_columns is not None:
        for expected in _EXPECTED_INDEXES:
            if expected.table != JOBS_TABLE:
                continue
            actual = _index_info(
                session_or_conn, table=expected.table, index=expected.name
            )
            if not _index_conforms(expected, actual):
                violations.append(f"{expected.name} index does not conform: {actual}")
        if not _fk_exists(
            session_or_conn,
            table=JOBS_TABLE,
            local_column="company_id",
            ref_table="companies",
            ref_column="id",
        ):
            violations.append(
                f"{JOBS_TABLE}.company_id -> companies.id foreign key is missing"
            )
        for table, name in _EXPECTED_CHECK_CONSTRAINTS:
            if table != JOBS_TABLE:
                continue
            if not _check_constraint_exists(session_or_conn, table=table, name=name):
                violations.append(f"{name} check constraint is missing on {table}")

    status = (
        ApplyReadinessStatus.CORRUPT
        if violations
        else ApplyReadinessStatus.FULLY_APPLIED
    )
    return ApplyReadiness(
        status=status,
        fields_columns=fields_columns,
        jobs_columns=jobs_columns,
        violations=tuple(violations),
    )


def company_enrichment_row_counts(conn: Any) -> dict[str, int]:
    """Row counts, read through the CALLER's own connection/transaction.
    Expected to be 0/0 immediately after --apply -- these are brand-new
    tables, nothing should have written to them yet."""
    counts = {"fields": 0, "jobs": 0}
    if _table_columns(conn, FIELDS_TABLE) is not None:
        counts["fields"] = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {FIELDS_TABLE}")).scalar_one()
        )
    if _table_columns(conn, JOBS_TABLE) is not None:
        counts["jobs"] = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {JOBS_TABLE}")).scalar_one()
        )
    return counts


def _apply_and_verify_within_transaction(conn: Any) -> dict[str, Any]:
    statements = company_enrichment_migration_statements()
    for statement in statements:
        conn.execute(text(statement))

    readiness = company_enrichment_apply_readiness(conn)
    if readiness.status is not ApplyReadinessStatus.FULLY_APPLIED:
        raise CompanyEnrichmentApplyPostconditionError(
            "Refusing to commit migration 034: schema does not fully conform to "
            "the expected contract immediately after applying its DDL.\n"
            "  Violations:\n" + "\n".join(f"    - {v}" for v in readiness.violations)
        )

    counts = company_enrichment_row_counts(conn)
    if counts["fields"] != 0 or counts["jobs"] != 0:
        raise CompanyEnrichmentApplyPostconditionError(
            f"Refusing to commit migration 034: found existing rows "
            f"({counts}) immediately after applying -- expected 0/0 for a "
            "brand-new schema."
        )

    return {
        "statements_executed": len(statements),
        "migration": "034_company_enrichment",
        "conforms": True,
        "row_counts": counts,
    }


def apply_company_enrichment_migration(engine: Engine) -> dict[str, Any]:
    """Apply migration 034 and verify its postcondition, all inside one
    transaction: execute the DDL, then -- through that exact same
    connection -- run the full schema-contract check and confirm both
    tables are empty. Any mismatch raises
    CompanyEnrichmentApplyPostconditionError, which (raised inside this
    ``with engine.begin()`` block) triggers an automatic ROLLBACK of the
    entire migration."""
    with engine.begin() as conn:
        return _apply_and_verify_within_transaction(conn)
