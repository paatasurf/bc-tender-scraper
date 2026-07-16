"""Classification Claims schema foundation (migration 029) — Class D helpers.

Applies six new, purely additive tables. Never touches any existing table
(``companies`` included) and never writes application data — this module's
only job is schema DDL and, for rollback, verifying the six tables are empty
before dropping them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.classification_claims_ddl import (
    classification_claims_migration_statements,
    classification_claims_rollback_statements,
    classification_claims_table_names,
)
from db.classification_claims_schema_contract import (
    SchemaConformanceResult,
    verify_schema_contract,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "ClassificationClaimsRollbackBlockedError",
    "ClassificationClaimsSchemaCorruptError",
    "classification_claims_apply_readiness",
    "classification_claims_before_stats",
    "classification_claims_migration_pending",
    "apply_classification_claims_migration",
    "classification_claims_row_counts",
    "apply_classification_claims_rollback",
]


class ClassificationClaimsRollbackBlockedError(RuntimeError):
    """Raised when rollback is attempted while any classification-claims
    table still contains rows. This migration never backfills or seeds
    data, so a non-empty table means something outside this migration's
    scope has written to it — rollback must not silently discard it."""


class ClassificationClaimsSchemaCorruptError(RuntimeError):
    """Raised when some (but not a schema-contract-conforming set of) the six
    classification-claims tables exist — e.g. a table is missing a column,
    CHECK constraint, index, or foreign key relative to
    db/migrations/029_classification_claims.sql. This is a fail-closed
    signal, not a repair: the operator must investigate (e.g. via
    scripts/run_classification_claims_schema_audit.py) and decide manually
    whether to fix forward with a new migration or drop and reapply. Nothing
    in this codebase attempts to silently repair a corrupt schema."""


def _existing_table_names(session_or_conn, names: list[str]) -> set[str]:
    rows = session_or_conn.execute(
        text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY(:names)
            """),
        {"names": names},
    ).all()
    return {row[0] for row in rows}


def classification_claims_before_stats(session: Session) -> dict[str, Any]:
    """Lightweight, table-existence-only snapshot — used only as the
    dry-run-artifact staleness signal (has the set of existing tables
    changed since the artifact was written?). Does NOT verify columns,
    constraints, or indexes — see classification_claims_apply_readiness()
    for the full schema-contract check used to gate --apply."""
    names = classification_claims_table_names()
    existing = _existing_table_names(session, names)
    return {
        "tables_expected": sorted(names),
        "tables_existing": sorted(existing),
        "tables_missing": sorted(set(names) - existing),
        "migration_pending": existing != set(names),
        "statements_planned": len(classification_claims_migration_statements()),
    }


def classification_claims_migration_pending(session: Session) -> bool:
    return bool(classification_claims_before_stats(session).get("migration_pending"))


class ApplyReadinessStatus(str, Enum):
    NOT_APPLIED = "not_applied"  # none of the six tables exist — safe to apply
    FULLY_APPLIED = "fully_applied"  # all six tables exist and fully match the contract
    CORRUPT = "corrupt"  # at least one table exists but the contract is not fully met


@dataclass(frozen=True)
class ApplyReadiness:
    status: ApplyReadinessStatus
    conformance: SchemaConformanceResult

    @property
    def violations(self) -> list[str]:
        return self.conformance.describe_violations()


def classification_claims_apply_readiness(session: Session) -> ApplyReadiness:
    """Full schema-contract check (columns/types/nullability/PK/UNIQUE/CHECK/
    indexes/FK — see db.classification_claims_schema_contract), not just
    table-name presence. This is what --apply consults before deciding
    whether to report "Already applied," proceed with a fresh apply, or
    fail closed as corrupt."""
    conformance = verify_schema_contract(session)
    if not conformance.any_table_exists:
        status = ApplyReadinessStatus.NOT_APPLIED
    elif conformance.fully_conforms:
        status = ApplyReadinessStatus.FULLY_APPLIED
    else:
        status = ApplyReadinessStatus.CORRUPT
    return ApplyReadiness(status=status, conformance=conformance)


def apply_classification_claims_migration(engine: Engine) -> dict[str, Any]:
    statements = classification_claims_migration_statements()
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    return {
        "statements_executed": len(statements),
        "migration": "029_classification_claims",
    }


def classification_claims_row_counts(engine: Engine) -> dict[str, int]:
    """Row count per table — 0 for any table that doesn't exist yet (nothing
    to roll back for it)."""
    names = classification_claims_table_names()
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        existing = _existing_table_names(conn, names)
        for name in names:
            if name in existing:
                counts[name] = int(
                    conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
                )
            else:
                counts[name] = 0
    return counts


def apply_classification_claims_rollback(engine: Engine) -> dict[str, Any]:
    """Atomic, all-or-nothing rollback.

    Everything — existence check, locking, emptiness check, and the DROP
    statements themselves — happens inside a single transaction:

    1. Determine which of the six tables currently exist.
    2. Acquire an ACCESS EXCLUSIVE lock on each existing table, in a fixed
       order (the DROP order), *before* checking row counts. This blocks
       any concurrent transaction from inserting into (or otherwise
       touching) these tables for the remainder of our transaction — closing
       the race where a concurrent INSERT could land between an emptiness
       check and the DROP statements if they were separate transactions.
    3. Check row counts (safe now — no concurrent writer can be modifying
       these tables while we hold the locks).
    4. If any table is non-empty, raise ClassificationClaimsRollbackBlockedError.
       Raising inside the `with engine.begin()` block triggers an automatic
       ROLLBACK of this transaction (releasing the locks, dropping nothing —
       true all-or-nothing: on any error, zero tables are dropped).
    5. Otherwise, execute the DROP TABLE IF EXISTS statements and commit.

    Missing tables are handled safely throughout: they are excluded from the
    lock-acquisition loop (LOCK TABLE on a nonexistent table would itself
    raise), contribute a row count of 0, and the rollback SQL's own
    DROP TABLE IF EXISTS is a no-op for a table that never existed.
    """
    names = classification_claims_table_names()  # reverse-creation (DROP) order
    with engine.begin() as conn:
        existing = _existing_table_names(conn, names)

        for name in names:
            if name in existing:
                conn.execute(text(f"LOCK TABLE {name} IN ACCESS EXCLUSIVE MODE"))

        counts: dict[str, int] = {}
        for name in names:
            if name in existing:
                counts[name] = int(
                    conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
                )
            else:
                counts[name] = 0

        non_empty = {name: n for name, n in counts.items() if n > 0}
        if non_empty:
            raise ClassificationClaimsRollbackBlockedError(
                "Refusing rollback: the following classification-claims tables are not "
                f"empty: {non_empty}. Investigate before proceeding — this migration never "
                "writes data itself, so non-empty rows came from something else."
            )

        statements = classification_claims_rollback_statements()
        for statement in statements:
            conn.execute(text(statement))

    return {
        "statements_executed": len(statements),
        "migration": "029_classification_claims_rollback",
        "row_counts_before_rollback": counts,
    }
