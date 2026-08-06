"""Runtime schema-contract checks and apply logic for the persistent
pipeline coordinator schema (migration 032) -- Class D helpers.

Applies two brand-new, empty tables (pipeline_coordinator_runs,
pipeline_coordinator_steps) plus their indexes -- never touches any
existing table, never writes application data. This module never applies
anything by itself; the only caller is
scripts/run_pipeline_coordinator_state_migration.py --apply.

No ORM mapping is added by this PR -- pipeline/run_coordinator_postgres.py
(selected via PIPELINE_COORDINATOR_BACKEND=postgres, see
pipeline/run_coordinator.py) talks to these tables through plain
SQLAlchemy Core Table objects (db/pipeline_coordinator_tables.py), which
are deliberately NOT part of db.models.Base.metadata, so
db.connection.init_db()'s Base.metadata.create_all() can never auto-create
this schema. That wiring decision is what this migration's runner enforces
operationally: the schema only exists once an operator has explicitly run
--apply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from db.pipeline_coordinator_ddl import (
    RUNS_TABLE,
    STEPS_TABLE,
    pipeline_coordinator_migration_statements,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "PipelineCoordinatorSchemaCorruptError",
    "PipelineCoordinatorApplyPostconditionError",
    "pipeline_coordinator_apply_readiness",
    "pipeline_coordinator_before_stats",
    "pipeline_coordinator_migration_pending",
    "apply_pipeline_coordinator_migration",
    "pipeline_coordinator_row_counts",
]


class PipelineCoordinatorSchemaCorruptError(RuntimeError):
    """Raised when the coordinator tables/indexes exist in a state that
    does not match db/migrations/032_pipeline_coordinator_state.sql --
    e.g. a table exists with a missing index, or a differently-shaped
    partial unique index. Fail-closed: the operator must investigate
    manually. Nothing here attempts to silently repair a corrupt schema."""


class PipelineCoordinatorApplyPostconditionError(RuntimeError):
    """Raised by apply_pipeline_coordinator_migration() when, immediately
    after executing migration 032's DDL -- through the exact same
    connection and transaction, before commit -- the resulting schema does
    not fully conform to the expected contract, or either table already
    has rows. Raised inside the same ``with engine.begin()`` block that
    ran the DDL, so it triggers an automatic ROLLBACK of the entire
    migration."""


_RUNS_EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "run_id",
        "pipeline_scope",
        "status",
        "phase",
        "tender_scrape_started_at",
        "tender_scrape_finished_at",
        "scrape_phase_started_at",
        "scrape_phase_finished_at",
        "import_started_at",
        "import_finished_at",
        "finished_at",
        "success",
        "error",
        "stale_reclaimed",
        "lease_expires_at",
        "created_at",
        "updated_at",
    }
)
_STEPS_EXPECTED_COLUMNS = frozenset({"id", "run_id", "step", "completed_at"})


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
        "ux_pipeline_coordinator_runs_run_id", RUNS_TABLE, True, ("run_id",)
    ),
    _ExpectedIndex(
        "ux_pipeline_coordinator_runs_active_scope",
        RUNS_TABLE,
        True,
        ("pipeline_scope",),
        predicate="status = 'active'",
    ),
    _ExpectedIndex(
        "ix_pipeline_coordinator_runs_scope_status",
        RUNS_TABLE,
        False,
        ("pipeline_scope", "status"),
    ),
    _ExpectedIndex(
        "ux_pipeline_coordinator_steps_run_step", STEPS_TABLE, True, ("run_id", "step")
    ),
    _ExpectedIndex(
        "ix_pipeline_coordinator_steps_run_id", STEPS_TABLE, False, ("run_id",)
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
            WHERE tc.table_name = :steps_table
              AND tc.constraint_type = 'FOREIGN KEY'
              AND ccu.table_name = :runs_table
              AND ccu.column_name = 'run_id'
            """),
        {"steps_table": STEPS_TABLE, "runs_table": RUNS_TABLE},
    ).first()
    return row is not None


def pipeline_coordinator_before_stats(session_or_conn: Any) -> dict[str, Any]:
    """Lightweight existence-only snapshot -- used as the dry-run-artifact
    staleness signal. Does NOT verify the full contract -- see
    pipeline_coordinator_apply_readiness() for that."""
    runs_columns = _table_columns(session_or_conn, RUNS_TABLE)
    steps_columns = _table_columns(session_or_conn, STEPS_TABLE)
    statements = pipeline_coordinator_migration_statements()
    return {
        "runs_table_exists": runs_columns is not None,
        "steps_table_exists": steps_columns is not None,
        "migration_pending": runs_columns is None or steps_columns is None,
        "statements_planned": len(statements),
    }


def pipeline_coordinator_migration_pending(session_or_conn: Any) -> bool:
    return bool(
        pipeline_coordinator_before_stats(session_or_conn).get("migration_pending")
    )


class ApplyReadinessStatus(str, Enum):
    NOT_APPLIED = "not_applied"  # neither table exists -- safe to apply
    FULLY_APPLIED = "fully_applied"  # both tables + all indexes + FK conform
    CORRUPT = "corrupt"  # something exists but doesn't fully match the contract


@dataclass(frozen=True)
class ApplyReadiness:
    status: ApplyReadinessStatus
    runs_columns: dict[str, str] | None
    steps_columns: dict[str, str] | None
    violations: tuple[str, ...]


def pipeline_coordinator_apply_readiness(session_or_conn: Any) -> ApplyReadiness:
    """Full schema-contract check: table + column-set + index (uniqueness,
    key columns, partial predicate) + foreign-key conformance -- not just
    existence. This is what --apply consults before deciding whether to
    report "Already applied," proceed, or fail closed as corrupt."""
    runs_columns = _table_columns(session_or_conn, RUNS_TABLE)
    steps_columns = _table_columns(session_or_conn, STEPS_TABLE)

    if runs_columns is None and steps_columns is None:
        return ApplyReadiness(
            status=ApplyReadinessStatus.NOT_APPLIED,
            runs_columns=None,
            steps_columns=None,
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

    if steps_columns is None:
        violations.append(f"{STEPS_TABLE} table is missing")
    elif set(steps_columns) != _STEPS_EXPECTED_COLUMNS:
        violations.append(
            f"{STEPS_TABLE} columns do not match: "
            f"missing={sorted(_STEPS_EXPECTED_COLUMNS - set(steps_columns))} "
            f"unexpected={sorted(set(steps_columns) - _STEPS_EXPECTED_COLUMNS)}"
        )

    if runs_columns is not None and steps_columns is not None:
        for expected in _EXPECTED_INDEXES:
            actual = _index_info(
                session_or_conn, table=expected.table, index=expected.name
            )
            if not _index_conforms(expected, actual):
                violations.append(f"{expected.name} index does not conform: {actual}")
        if not _fk_exists(session_or_conn):
            violations.append(
                f"{STEPS_TABLE}.run_id -> {RUNS_TABLE}.run_id foreign key is missing"
            )

    status = (
        ApplyReadinessStatus.CORRUPT
        if violations
        else ApplyReadinessStatus.FULLY_APPLIED
    )
    return ApplyReadiness(
        status=status,
        runs_columns=runs_columns,
        steps_columns=steps_columns,
        violations=tuple(violations),
    )


def pipeline_coordinator_row_counts(conn: Any) -> dict[str, int]:
    """Row counts, read through the CALLER's own connection/transaction.
    Expected to be 0/0 immediately after --apply -- these are brand-new
    tables, nothing should have written to them yet."""
    counts = {"runs": 0, "steps": 0}
    if _table_columns(conn, RUNS_TABLE) is not None:
        counts["runs"] = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {RUNS_TABLE}")).scalar_one()
        )
    if _table_columns(conn, STEPS_TABLE) is not None:
        counts["steps"] = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {STEPS_TABLE}")).scalar_one()
        )
    return counts


def _apply_and_verify_within_transaction(conn: Any) -> dict[str, Any]:
    statements = pipeline_coordinator_migration_statements()
    for statement in statements:
        conn.execute(text(statement))

    readiness = pipeline_coordinator_apply_readiness(conn)
    if readiness.status is not ApplyReadinessStatus.FULLY_APPLIED:
        raise PipelineCoordinatorApplyPostconditionError(
            "Refusing to commit migration 032: schema does not fully conform to "
            "the expected contract immediately after applying its DDL.\n"
            "  Violations:\n" + "\n".join(f"    - {v}" for v in readiness.violations)
        )

    counts = pipeline_coordinator_row_counts(conn)
    if counts["runs"] != 0 or counts["steps"] != 0:
        raise PipelineCoordinatorApplyPostconditionError(
            f"Refusing to commit migration 032: found existing rows "
            f"({counts}) immediately after applying -- expected 0/0 for a "
            "brand-new schema."
        )

    return {
        "statements_executed": len(statements),
        "migration": "032_pipeline_coordinator_state",
        "conforms": True,
        "row_counts": counts,
    }


def apply_pipeline_coordinator_migration(engine: Engine) -> dict[str, Any]:
    """Apply migration 032 and verify its postcondition, all inside one
    transaction: execute the DDL, then -- through that exact same
    connection -- run the full schema-contract check and confirm both
    tables are empty. Any mismatch raises
    PipelineCoordinatorApplyPostconditionError, which (raised inside this
    ``with engine.begin()`` block) triggers an automatic ROLLBACK of the
    entire migration."""
    with engine.begin() as conn:
        return _apply_and_verify_within_transaction(conn)
