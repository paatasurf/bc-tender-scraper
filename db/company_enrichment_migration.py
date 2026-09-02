"""Runtime schema-contract checks and apply logic for the company
on-demand enrichment schema (migration 034, RFC Phase 1) and its Phase 3
provenance/verification extension (migration 035,
docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2) -- Class D helpers.

Migration 034 applies two brand-new, empty tables (company_enrichment_fields,
company_enrichment_jobs) plus their indexes -- never touches any existing
table (including companies), never writes application data. Migration 035
ALTERs those same two existing tables (6 new columns + 1 CHECK constraint
on company_enrichment_fields; 1 new JSONB column + 1 validation function +
1 CHECK constraint on company_enrichment_jobs) -- still touches no other
table, still writes no application data, and does NOT require either table
to be empty (unlike 034's own postcondition, which does -- 034 creates the
tables from nothing, 035 alters tables that may already hold real rows).
This module never applies anything by itself; the only callers are
scripts/run_company_enrichment_migration.py --apply (034) and
scripts/run_company_enrichment_phase3_migration.py --apply (035).

No ORM mapping is added by this PR -- pipeline/company_enrichment/* talks
to these tables through plain SQLAlchemy Core Table objects
(db/company_enrichment_tables.py), which are deliberately NOT part of
db.models.Base.metadata, so db.connection.init_db()'s
Base.metadata.create_all() can never auto-create this schema. That wiring
decision is what this migration's runner enforces operationally: the
schema only exists once an operator has explicitly run --apply. Mirrors
db/ops_job_run_migration.py's approach (migration 033) exactly.

Phase 3 readiness (company_enrichment_phase3_apply_readiness()) is a
SEPARATE function from 034's own company_enrichment_apply_readiness(),
gated on 034 already being FULLY_APPLIED as a precondition -- "034
applied, Phase 3 not yet applied" is a valid, normal, expected state (not
corruption), and must stay distinguishable from "Phase 3 partially
applied" (which IS corruption). 034's own readiness function is updated
only so that the six/one Phase 3 columns, if ALSO present, are never
misreported as "unexpected" columns (design doc S2.5) -- it otherwise
still reports FULLY_APPLIED for the original 034-only schema exactly as
before Phase 3 existed.
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
    company_enrichment_phase3_migration_statements,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "CompanyEnrichmentSchemaCorruptError",
    "CompanyEnrichmentApplyPostconditionError",
    "CompanyEnrichmentPhase3SchemaCorruptError",
    "CompanyEnrichmentPhase3ApplyPostconditionError",
    "company_enrichment_apply_readiness",
    "company_enrichment_before_stats",
    "company_enrichment_migration_pending",
    "apply_company_enrichment_migration",
    "company_enrichment_row_counts",
    "company_enrichment_phase3_apply_readiness",
    "company_enrichment_phase3_before_stats",
    "company_enrichment_phase3_migration_pending",
    "apply_company_enrichment_phase3_migration",
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


class CompanyEnrichmentPhase3SchemaCorruptError(RuntimeError):
    """Raised when migration 034 is applied but the Phase 3 increment
    (db/migrations/035_company_enrichment_phase3.sql) exists in a state
    that does not match it -- e.g. only some of the 6 new
    company_enrichment_fields columns are present, or a same-named CHECK
    constraint exists with the wrong expression. Fail-closed: the operator
    must investigate manually. Nothing here attempts to silently repair a
    corrupt schema."""


class CompanyEnrichmentPhase3ApplyPostconditionError(RuntimeError):
    """Raised by apply_company_enrichment_phase3_migration() when
    migration 034 is not FULLY_APPLIED (Phase 3 cannot be safely applied
    on top of a table that doesn't fully exist yet), or when, immediately
    after executing migration 035's DDL -- through the exact same
    connection and transaction, before commit -- the resulting schema does
    not fully conform to the expected Phase 3 contract. Raised inside the
    same ``with engine.begin()`` block that ran the DDL (when raised after
    the DDL executes), so it triggers an automatic ROLLBACK of the entire
    migration."""


# The original migration-034 column set for each table -- REQUIRED for
# 034's own FULLY_APPLIED status regardless of whether Phase 3 is also
# applied on top.
_FIELDS_034_COLUMNS = frozenset(
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
_JOBS_034_COLUMNS = frozenset(
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

# Migration 035 (Phase 3) additions -- design doc S2.2/S2.1. OPTIONAL for
# 034's own readiness (their presence must never be flagged as
# "unexpected" there -- design doc S2.5), REQUIRED for
# company_enrichment_phase3_apply_readiness()'s own FULLY_APPLIED status.
_FIELDS_PHASE3_COLUMNS = frozenset(
    {
        "source_url",
        "raw_value",
        "extraction_method",
        "verified_at",
        "verified_by",
        "verification_source_url",
    }
)
_JOBS_PHASE3_COLUMNS = frozenset({"field_attempt_log"})

# Every column either migration could legitimately have created -- used by
# 034's OWN readiness so a Phase-3-extended schema is never reported as
# containing "unexpected" columns; 034's readiness still separately
# requires the full _FIELDS_034_COLUMNS/_JOBS_034_COLUMNS set to be
# present regardless (see company_enrichment_apply_readiness() below).
_FIELDS_KNOWN_COLUMNS = _FIELDS_034_COLUMNS | _FIELDS_PHASE3_COLUMNS
_JOBS_KNOWN_COLUMNS = _JOBS_034_COLUMNS | _JOBS_PHASE3_COLUMNS

_VALIDATE_FIELD_ATTEMPT_LOG_FUNCTION = "company_enrichment_validate_field_attempt_log"


@dataclass(frozen=True)
class _ExpectedCheckConstraintShape:
    """A Phase 3 CHECK constraint verified by actual expression shape (via
    pg_get_constraintdef), not just name existence -- mirrors
    _index_conforms()'s existing pg_get_expr-based predicate comparison
    for indexes. Deliberately NOT applied to the two pre-existing 034
    constraints (ck_company_enrichment_jobs_trigger/_status, still checked
    by _check_constraint_exists() name-only below): db/ops_job_run_migration.py's
    own _EXPECTED_CHECK_CONSTRAINTS explicitly documents that Postgres
    rewrites an `IN (...)` clause on a varchar column in a
    version-dependent way, making exact-text comparison fragile for THAT
    shape of constraint. Phase 3's two constraints are NOT `IN (...)`
    clauses (a function call and an IS-NOT-NULL conjunction), so the
    fragility that precedent warns about doesn't apply to them, and the
    schema review's own empirical finding (a same-named-but-weaker
    verified-evidence constraint silently surviving the DO $$ guard) is
    exactly the corruption shape-comparison is needed to catch."""

    table: str
    name: str
    expected_definition: str  # a real pg_get_constraintdef()-shaped string


_PHASE3_EXPECTED_CHECK_CONSTRAINTS: tuple[_ExpectedCheckConstraintShape, ...] = (
    _ExpectedCheckConstraintShape(
        JOBS_TABLE,
        "ck_company_enrichment_jobs_field_attempt_log_shape",
        f"CHECK ({_VALIDATE_FIELD_ATTEMPT_LOG_FUNCTION}(field_attempt_log))",
    ),
    _ExpectedCheckConstraintShape(
        FIELDS_TABLE,
        "ck_company_enrichment_fields_verified_evidence",
        "CHECK (((NOT verified) OR ((verified_by IS NOT NULL) AND "
        "(verified_at IS NOT NULL) AND (verification_source_url IS NOT NULL))))",
    ),
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


_CHECK_CONSTRAINT_DEF_SQL = text("""
    SELECT pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE con.contype = 'c'
      AND con.conname = :name
      AND rel.relname = :table
      AND nsp.nspname = 'public'
    """)


def _check_constraint_def(conn: Any, *, table: str, name: str) -> str | None:
    row = conn.execute(
        _CHECK_CONSTRAINT_DEF_SQL, {"table": table, "name": name}
    ).first()
    return row[0] if row is not None else None


def _check_constraint_conforms(
    conn: Any, expected: _ExpectedCheckConstraintShape
) -> bool:
    """Shape comparison (not just name existence) for a Phase 3 CHECK
    constraint -- reuses _normalize_predicate()'s cast-stripping/
    paren-stripping/whitespace-collapsing normalization (already proven
    correct for index predicates) so this is robust to Postgres's own
    cosmetic formatting of pg_get_constraintdef() output, while still
    catching a genuinely different expression (design doc S2.2's
    documented "same name, weaker CHECK" blind spot -- exactly what a
    name-only DO $$ guard cannot detect, and exactly what this function
    exists to catch instead)."""
    actual = _check_constraint_def(conn, table=expected.table, name=expected.name)
    if actual is None:
        return False
    return _normalize_predicate(actual) == _normalize_predicate(
        expected.expected_definition
    )


_VALIDATE_FUNCTION_EXISTS_SQL = text("""
    SELECT 1
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = :name AND n.nspname = 'public'
    """)


def _function_exists(conn: Any, *, name: str) -> bool:
    row = conn.execute(_VALIDATE_FUNCTION_EXISTS_SQL, {"name": name}).first()
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

    # Column-set check: the original 034 columns are always REQUIRED
    # ("missing" against _FIELDS_034_COLUMNS/_JOBS_034_COLUMNS); a column
    # is only "unexpected" if it isn't part of EITHER 034's or Phase 3's
    # known contribution (_FIELDS_KNOWN_COLUMNS/_JOBS_KNOWN_COLUMNS) --
    # design doc S2.5: the moment Phase 3's columns exist (correctly, on
    # purpose), 034's own readiness must keep reporting FULLY_APPLIED, not
    # CORRUPT, exactly as it did before Phase 3 existed.
    if fields_columns is None:
        violations.append(f"{FIELDS_TABLE} table is missing")
    else:
        missing = _FIELDS_034_COLUMNS - set(fields_columns)
        unexpected = set(fields_columns) - _FIELDS_KNOWN_COLUMNS
        if missing or unexpected:
            violations.append(
                f"{FIELDS_TABLE} columns do not match: "
                f"missing={sorted(missing)} unexpected={sorted(unexpected)}"
            )

    if jobs_columns is None:
        violations.append(f"{JOBS_TABLE} table is missing")
    else:
        missing = _JOBS_034_COLUMNS - set(jobs_columns)
        unexpected = set(jobs_columns) - _JOBS_KNOWN_COLUMNS
        if missing or unexpected:
            violations.append(
                f"{JOBS_TABLE} columns do not match: "
                f"missing={sorted(missing)} unexpected={sorted(unexpected)}"
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


# ---------------------------------------------------------------------------
# Phase 3 (migration 035) -- provenance/verification schema extension.
# Separate readiness/apply functions from 034's own, gated on 034 already
# being FULLY_APPLIED (design doc S2.5).
# ---------------------------------------------------------------------------


def company_enrichment_phase3_before_stats(session_or_conn: Any) -> dict[str, Any]:
    """Lightweight existence-only snapshot -- used as the dry-run-artifact
    staleness signal. Does NOT verify the full contract -- see
    company_enrichment_phase3_apply_readiness() for that."""
    fields_columns = _table_columns(session_or_conn, FIELDS_TABLE)
    jobs_columns = _table_columns(session_or_conn, JOBS_TABLE)
    statements = company_enrichment_phase3_migration_statements()
    fields_phase3_present = bool(_FIELDS_PHASE3_COLUMNS & set(fields_columns or ()))
    jobs_phase3_present = bool(_JOBS_PHASE3_COLUMNS & set(jobs_columns or ()))
    return {
        "fields_table_exists": fields_columns is not None,
        "jobs_table_exists": jobs_columns is not None,
        "phase3_columns_present": fields_phase3_present or jobs_phase3_present,
        "migration_pending": not (fields_phase3_present or jobs_phase3_present),
        "statements_planned": len(statements),
    }


def company_enrichment_phase3_migration_pending(session_or_conn: Any) -> bool:
    return bool(
        company_enrichment_phase3_before_stats(session_or_conn).get("migration_pending")
    )


def company_enrichment_phase3_apply_readiness(session_or_conn: Any) -> ApplyReadiness:
    """Phase 3 (migration 035) schema-contract check: the 6 new
    company_enrichment_fields columns, the 1 new company_enrichment_jobs
    column, the validation function, and both new CHECK constraints
    (verified by actual expression shape, not just name) -- independent of,
    but gated on, migration 034's own readiness.

    Reuses ApplyReadiness/ApplyReadinessStatus (same three-state shape as
    034's own readiness) rather than inventing a parallel type -- the
    meaning is identical ("is this specific migration's contract fully,
    partially, or not-at-all satisfied"), just evaluated against a
    different, later increment of the same two tables.

    "034 applied, Phase 3 not yet applied" reports NOT_APPLIED here (a
    valid, normal, expected state -- not corruption). "034 not
    FULLY_APPLIED at all" (including a totally fresh, untouched Postgres)
    also reports NOT_APPLIED when nothing exists yet, or CORRUPT with an
    explicit "034 must be applied first" violation when 034 itself is in
    a CORRUPT state -- Phase 3's readiness has no meaningful independent
    answer to give in that case (design doc S2.5)."""
    base = company_enrichment_apply_readiness(session_or_conn)

    if base.status is ApplyReadinessStatus.NOT_APPLIED:
        # Neither table exists at all -- Phase 3 is trivially not applied
        # too. The normal "haven't started" state, not corruption.
        return ApplyReadiness(
            status=ApplyReadinessStatus.NOT_APPLIED,
            fields_columns=None,
            jobs_columns=None,
            violations=(),
        )

    if base.status is ApplyReadinessStatus.CORRUPT:
        return ApplyReadiness(
            status=ApplyReadinessStatus.CORRUPT,
            fields_columns=base.fields_columns,
            jobs_columns=base.jobs_columns,
            violations=(
                "migration 034 must be FULLY_APPLIED before Phase 3 readiness "
                "can be evaluated; 034 violations: " + "; ".join(base.violations),
            ),
        )

    # base.status is FULLY_APPLIED: 034's own required columns are all
    # present and correctly shaped. Now independently check Phase 3's own
    # increment: 6 fields columns, 1 jobs column, 1 function, 2 constraints.
    fields_columns = base.fields_columns or {}
    jobs_columns = base.jobs_columns or {}

    present_fields_phase3 = _FIELDS_PHASE3_COLUMNS & set(fields_columns)
    present_jobs_phase3 = _JOBS_PHASE3_COLUMNS & set(jobs_columns)
    function_present = _function_exists(
        session_or_conn, name=_VALIDATE_FIELD_ATTEMPT_LOG_FUNCTION
    )
    constraint_exists_by_name = {
        expected.name: _check_constraint_exists(
            session_or_conn, table=expected.table, name=expected.name
        )
        for expected in _PHASE3_EXPECTED_CHECK_CONSTRAINTS
    }

    nothing_present = (
        not present_fields_phase3
        and not present_jobs_phase3
        and not function_present
        and not any(constraint_exists_by_name.values())
    )
    if nothing_present:
        return ApplyReadiness(
            status=ApplyReadinessStatus.NOT_APPLIED,
            fields_columns=fields_columns,
            jobs_columns=jobs_columns,
            violations=(),
        )

    violations: list[str] = []

    missing_fields = _FIELDS_PHASE3_COLUMNS - set(fields_columns)
    if missing_fields:
        violations.append(
            f"{FIELDS_TABLE} missing Phase 3 columns: {sorted(missing_fields)}"
        )

    missing_jobs = _JOBS_PHASE3_COLUMNS - set(jobs_columns)
    if missing_jobs:
        violations.append(
            f"{JOBS_TABLE} missing Phase 3 columns: {sorted(missing_jobs)}"
        )

    if not function_present:
        violations.append(
            f"{_VALIDATE_FIELD_ATTEMPT_LOG_FUNCTION}() function is missing"
        )

    for expected in _PHASE3_EXPECTED_CHECK_CONSTRAINTS:
        if not constraint_exists_by_name[expected.name]:
            violations.append(
                f"{expected.name} check constraint is missing on {expected.table}"
            )
        elif not _check_constraint_conforms(session_or_conn, expected):
            actual_def = _check_constraint_def(
                session_or_conn, table=expected.table, name=expected.name
            )
            violations.append(
                f"{expected.name} check constraint does not conform: "
                f"actual={actual_def!r} expected={expected.expected_definition!r}"
            )

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


def _apply_and_verify_phase3_within_transaction(conn: Any) -> dict[str, Any]:
    base_readiness = company_enrichment_apply_readiness(conn)
    if base_readiness.status is not ApplyReadinessStatus.FULLY_APPLIED:
        raise CompanyEnrichmentPhase3ApplyPostconditionError(
            "Refusing to apply migration 035 (Phase 3): migration 034 is not "
            f"FULLY_APPLIED (status={base_readiness.status.value}).\n"
            "  034 violations:\n"
            + "\n".join(f"    - {v}" for v in base_readiness.violations)
        )

    statements = company_enrichment_phase3_migration_statements()
    for statement in statements:
        conn.execute(text(statement))

    readiness = company_enrichment_phase3_apply_readiness(conn)
    if readiness.status is not ApplyReadinessStatus.FULLY_APPLIED:
        raise CompanyEnrichmentPhase3ApplyPostconditionError(
            "Refusing to commit migration 035 (Phase 3): schema does not fully "
            "conform to the expected contract immediately after applying its "
            "DDL.\n  Violations:\n"
            + "\n".join(f"    - {v}" for v in readiness.violations)
        )

    # Unlike 034's own apply, Phase 3 does NOT require either table to be
    # empty -- it alters existing tables that may already hold real rows
    # (new nullable columns / a JSONB column with a '[]' default never
    # rejects an existing row). Row counts are reported for visibility
    # only, never as a postcondition.
    counts = company_enrichment_row_counts(conn)

    return {
        "statements_executed": len(statements),
        "migration": "035_company_enrichment_phase3",
        "conforms": True,
        "row_counts": counts,
    }


def apply_company_enrichment_phase3_migration(engine: Engine) -> dict[str, Any]:
    """Apply migration 035 (Phase 3) and verify its postcondition, all
    inside one transaction: confirm 034 is FULLY_APPLIED, execute the
    Phase 3 DDL, then -- through that exact same connection -- run the
    full Phase 3 schema-contract check. Any mismatch raises
    CompanyEnrichmentPhase3ApplyPostconditionError, which (raised inside
    this ``with engine.begin()`` block) triggers an automatic ROLLBACK of
    the entire migration."""
    with engine.begin() as conn:
        return _apply_and_verify_phase3_within_transaction(conn)
