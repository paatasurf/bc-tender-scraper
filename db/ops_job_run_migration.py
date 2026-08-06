"""Runtime schema-contract checks and apply logic for the ops job run
schema (migration 033, M3B) -- Class D helpers.

Applies two brand-new, empty tables (ops_job_runs, ops_job_run_events)
plus their indexes -- never touches any existing table, never writes
application data. This module never applies anything by itself; the only
caller is scripts/run_ops_job_run_migration.py --apply.

No ORM mapping is added by this PR -- pipeline/job_run.py talks to these
tables through plain SQLAlchemy Core Table objects
(db/ops_job_run_tables.py), which are deliberately NOT part of
db.models.Base.metadata, so db.connection.init_db()'s
Base.metadata.create_all() can never auto-create this schema. That wiring
decision is what this migration's runner enforces operationally: the
schema only exists once an operator has explicitly run --apply. Mirrors
db/pipeline_coordinator_migration.py's approach (migration 032) exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from db.ops_job_run_ddl import (
    EVENTS_TABLE,
    RUNS_TABLE,
    ops_job_run_migration_statements,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "OpsJobRunSchemaCorruptError",
    "OpsJobRunApplyPostconditionError",
    "ops_job_run_apply_readiness",
    "ops_job_run_before_stats",
    "ops_job_run_migration_pending",
    "apply_ops_job_run_migration",
    "ops_job_run_row_counts",
]


class OpsJobRunSchemaCorruptError(RuntimeError):
    """Raised when the job run tables/indexes exist in a state that does
    not match db/migrations/033_ops_job_runs.sql -- e.g. a table exists
    with a missing index, or a differently-shaped partial unique index.
    Fail-closed: the operator must investigate manually. Nothing here
    attempts to silently repair a corrupt schema."""


class OpsJobRunApplyPostconditionError(RuntimeError):
    """Raised by apply_ops_job_run_migration() when, immediately after
    executing migration 033's DDL -- through the exact same connection and
    transaction, before commit -- the resulting schema does not fully
    conform to the expected contract, or either table already has rows.
    Raised inside the same ``with engine.begin()`` block that ran the
    DDL, so it triggers an automatic ROLLBACK of the entire migration."""


_RUNS_EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "run_id",
        "job_type",
        "source",
        "trigger",
        "status",
        "started_at",
        "heartbeat_at",
        "finished_at",
        "lease_expires_at",
        "counts",
        "error_present",
        "error_summary",
        "idempotency_key",
        "created_at",
        "updated_at",
    }
)
_EVENTS_EXPECTED_COLUMNS = frozenset(
    {"id", "run_id", "event_type", "step", "counts_delta", "occurred_at"}
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
    _ExpectedIndex("ux_ops_job_runs_run_id", RUNS_TABLE, True, ("run_id",)),
    _ExpectedIndex(
        "ux_ops_job_runs_job_type_idempotency_key",
        RUNS_TABLE,
        True,
        ("job_type", "idempotency_key"),
        predicate="idempotency_key is not null",
    ),
    _ExpectedIndex(
        "ix_ops_job_runs_job_type_status", RUNS_TABLE, False, ("job_type", "status")
    ),
    _ExpectedIndex(
        "ix_ops_job_runs_status_lease_expires_at",
        RUNS_TABLE,
        False,
        ("status", "lease_expires_at"),
    ),
    _ExpectedIndex(
        "ix_ops_job_run_events_run_id_occurred_at",
        EVENTS_TABLE,
        False,
        ("run_id", "occurred_at"),
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


def _fk_exists(conn: Any) -> bool:
    row = conn.execute(
        text("""
            SELECT 1
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
            WHERE tc.table_name = :events_table
              AND tc.constraint_type = 'FOREIGN KEY'
              AND ccu.table_name = :runs_table
              AND ccu.column_name = 'run_id'
            """),
        {"events_table": EVENTS_TABLE, "runs_table": RUNS_TABLE},
    ).first()
    return row is not None


# CHECK constraints are verified by NAME existence only (not exact
# rewritten-expression text, which Postgres normalizes in a
# version-dependent way for `IN (...)` clauses on varchar columns -- see
# how _index_conforms() already needs _normalize_predicate() just for
# partial-index predicates). A missing constraint is exactly the
# corruption signal this check exists to catch; matching the FK-existence
# check's precedent (_fk_exists()) rather than the deeper index-structure
# comparison.
_EXPECTED_CHECK_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (RUNS_TABLE, "ck_ops_job_runs_trigger"),
    (RUNS_TABLE, "ck_ops_job_runs_status"),
    (EVENTS_TABLE, "ck_ops_job_run_events_event_type"),
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


def ops_job_run_before_stats(session_or_conn: Any) -> dict[str, Any]:
    """Lightweight existence-only snapshot -- used as the dry-run-artifact
    staleness signal. Does NOT verify the full contract -- see
    ops_job_run_apply_readiness() for that."""
    runs_columns = _table_columns(session_or_conn, RUNS_TABLE)
    events_columns = _table_columns(session_or_conn, EVENTS_TABLE)
    statements = ops_job_run_migration_statements()
    return {
        "runs_table_exists": runs_columns is not None,
        "events_table_exists": events_columns is not None,
        "migration_pending": runs_columns is None or events_columns is None,
        "statements_planned": len(statements),
    }


def ops_job_run_migration_pending(session_or_conn: Any) -> bool:
    return bool(ops_job_run_before_stats(session_or_conn).get("migration_pending"))


class ApplyReadinessStatus(str, Enum):
    NOT_APPLIED = "not_applied"  # neither table exists -- safe to apply
    FULLY_APPLIED = "fully_applied"  # both tables + all indexes + FK conform
    CORRUPT = "corrupt"  # something exists but doesn't fully match the contract


@dataclass(frozen=True)
class ApplyReadiness:
    status: ApplyReadinessStatus
    runs_columns: dict[str, str] | None
    events_columns: dict[str, str] | None
    violations: tuple[str, ...]


def ops_job_run_apply_readiness(session_or_conn: Any) -> ApplyReadiness:
    """Full schema-contract check: table + column-set + index (uniqueness,
    key columns, partial predicate) + foreign-key + CHECK-constraint
    conformance -- not just existence. This is what --apply consults
    before deciding whether to report "Already applied," proceed, or
    fail closed as corrupt."""
    runs_columns = _table_columns(session_or_conn, RUNS_TABLE)
    events_columns = _table_columns(session_or_conn, EVENTS_TABLE)

    if runs_columns is None and events_columns is None:
        return ApplyReadiness(
            status=ApplyReadinessStatus.NOT_APPLIED,
            runs_columns=None,
            events_columns=None,
            violations=(),
        )

    violations: list[str] = []

    if runs_columns is None:
        violations.append(f"{RUNS_TABLE} table is missing")
    elif set(runs_columns) != _RUNS_EXPECTED_COLUMNS:
        violations.append(
            f"{RUNS_TABLE} columns do not match: "
            f"missing={sorted(_RUNS_EXPECTED_COLUMNS - set(runs_columns))} "
            f"unexpected={sorted(set(runs_columns) - _RUNS_EXPECTED_COLUMNS)}"
        )

    if events_columns is None:
        violations.append(f"{EVENTS_TABLE} table is missing")
    elif set(events_columns) != _EVENTS_EXPECTED_COLUMNS:
        violations.append(
            f"{EVENTS_TABLE} columns do not match: "
            f"missing={sorted(_EVENTS_EXPECTED_COLUMNS - set(events_columns))} "
            f"unexpected={sorted(set(events_columns) - _EVENTS_EXPECTED_COLUMNS)}"
        )

    if runs_columns is not None and events_columns is not None:
        for expected in _EXPECTED_INDEXES:
            actual = _index_info(
                session_or_conn, table=expected.table, index=expected.name
            )
            if not _index_conforms(expected, actual):
                violations.append(f"{expected.name} index does not conform: {actual}")
        if not _fk_exists(session_or_conn):
            violations.append(
                f"{EVENTS_TABLE}.run_id -> {RUNS_TABLE}.run_id foreign key is missing"
            )
        for table, name in _EXPECTED_CHECK_CONSTRAINTS:
            if not _check_constraint_exists(session_or_conn, table=table, name=name):
                violations.append(f"{name} check constraint is missing on {table}")

    status = (
        ApplyReadinessStatus.CORRUPT
        if violations
        else ApplyReadinessStatus.FULLY_APPLIED
    )
    return ApplyReadiness(
        status=status,
        runs_columns=runs_columns,
        events_columns=events_columns,
        violations=tuple(violations),
    )


def ops_job_run_row_counts(conn: Any) -> dict[str, int]:
    """Row counts, read through the CALLER's own connection/transaction.
    Expected to be 0/0 immediately after --apply -- these are brand-new
    tables, nothing should have written to them yet."""
    counts = {"runs": 0, "events": 0}
    if _table_columns(conn, RUNS_TABLE) is not None:
        counts["runs"] = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {RUNS_TABLE}")).scalar_one()
        )
    if _table_columns(conn, EVENTS_TABLE) is not None:
        counts["events"] = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {EVENTS_TABLE}")).scalar_one()
        )
    return counts


def _apply_and_verify_within_transaction(conn: Any) -> dict[str, Any]:
    statements = ops_job_run_migration_statements()
    for statement in statements:
        conn.execute(text(statement))

    readiness = ops_job_run_apply_readiness(conn)
    if readiness.status is not ApplyReadinessStatus.FULLY_APPLIED:
        raise OpsJobRunApplyPostconditionError(
            "Refusing to commit migration 033: schema does not fully conform to "
            "the expected contract immediately after applying its DDL.\n"
            "  Violations:\n" + "\n".join(f"    - {v}" for v in readiness.violations)
        )

    counts = ops_job_run_row_counts(conn)
    if counts["runs"] != 0 or counts["events"] != 0:
        raise OpsJobRunApplyPostconditionError(
            f"Refusing to commit migration 033: found existing rows "
            f"({counts}) immediately after applying -- expected 0/0 for a "
            "brand-new schema."
        )

    return {
        "statements_executed": len(statements),
        "migration": "033_ops_job_runs",
        "conforms": True,
        "row_counts": counts,
    }


def apply_ops_job_run_migration(engine: Engine) -> dict[str, Any]:
    """Apply migration 033 and verify its postcondition, all inside one
    transaction: execute the DDL, then -- through that exact same
    connection -- run the full schema-contract check and confirm both
    tables are empty. Any mismatch raises
    OpsJobRunApplyPostconditionError, which (raised inside this
    ``with engine.begin()`` block) triggers an automatic ROLLBACK of the
    entire migration."""
    with engine.begin() as conn:
        return _apply_and_verify_within_transaction(conn)
