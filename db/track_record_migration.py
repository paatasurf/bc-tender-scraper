"""Company track-record schema foundation (migration 030) -- Class D helpers.

Applies four new, purely additive, nullable columns (plus three CHECK
constraints) on the existing ``companies`` table. Never touches any other
table, never writes application data, and never adds a default -- every
existing row is left with all four columns NULL after apply.

No rollback helper is provided here (deliberate scope decision, unlike
migration 029's paired forward+rollback): ``ADD COLUMN IF NOT EXISTS`` +
idempotency-guarded ``ADD CONSTRAINT`` is inert and side-effect-free for
every other code path until a future PR starts writing to these columns,
so there is nothing destructive here that needs a companion rollback path
yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.track_record_ddl import (
    company_track_record_column_names,
    company_track_record_migration_statements,
    company_track_record_migration_touches_only_companies,
)
from db.track_record_schema_contract import (
    TABLE_NAME,
    TrackRecordConformance,
    verify_track_record_schema_contract,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "CompanyTrackRecordSchemaCorruptError",
    "CompanyTrackRecordApplyPostconditionError",
    "company_track_record_apply_readiness",
    "company_track_record_before_stats",
    "company_track_record_migration_pending",
    "apply_company_track_record_migration",
    "company_track_record_nonnull_row_count",
    "build_track_record_audit_report",
]


class CompanyTrackRecordSchemaCorruptError(RuntimeError):
    """Raised when some (but not a schema-contract-conforming set of) the
    four track_record_* columns/constraints exist on companies -- e.g. a
    column is missing a CHECK constraint, has the wrong type, or
    unexpectedly has a default, relative to
    db/migrations/030_company_track_record.sql. This is a fail-closed
    signal, not a repair: the operator must investigate (e.g. via
    scripts/run_company_track_record_schema_audit.py) and decide manually
    whether to fix forward with a new migration. Nothing in this codebase
    attempts to silently repair a corrupt schema."""


class CompanyTrackRecordApplyPostconditionError(RuntimeError):
    """Raised by apply_company_track_record_migration() when, immediately
    after executing migration 030's DDL -- through the exact same
    connection and transaction, before commit -- the resulting schema
    does not fully conform to the expected contract, or any row already
    has a non-NULL track_record_* value. Raised inside the same
    ``with engine.begin()`` block that ran the DDL, so it triggers an
    automatic ROLLBACK of the entire migration: nothing is committed
    unless both postconditions are confirmed first."""


def _existing_column_names(session_or_conn, names: list[str]) -> set[str]:
    rows = session_or_conn.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
              AND column_name = ANY(:names)
            """),
        {"t": TABLE_NAME, "names": names},
    ).all()
    return {row[0] for row in rows}


def company_track_record_before_stats(session: Session) -> dict[str, Any]:
    """Lightweight, column-existence-only snapshot -- used only as the
    dry-run-artifact staleness signal (has the set of existing columns
    changed since the artifact was written?). Does NOT verify types,
    nullability, or CHECK constraints -- see
    company_track_record_apply_readiness() for the full schema-contract
    check used to gate --apply."""
    names = company_track_record_column_names()
    existing = _existing_column_names(session, names)
    return {
        "columns_expected": sorted(names),
        "columns_existing": sorted(existing),
        "columns_missing": sorted(set(names) - existing),
        "migration_pending": existing != set(names),
        "statements_planned": len(company_track_record_migration_statements()),
    }


def company_track_record_migration_pending(session: Session) -> bool:
    return bool(company_track_record_before_stats(session).get("migration_pending"))


class ApplyReadinessStatus(str, Enum):
    NOT_APPLIED = "not_applied"  # none of the 4 columns exist -- safe to apply
    FULLY_APPLIED = "fully_applied"  # all 4 columns + 3 checks fully match the contract
    CORRUPT = "corrupt"  # at least one column exists but the contract is not fully met


@dataclass(frozen=True)
class ApplyReadiness:
    status: ApplyReadinessStatus
    conformance: TrackRecordConformance

    @property
    def violations(self) -> list[str]:
        return self.conformance.describe_violations()


def company_track_record_apply_readiness(session: Session) -> ApplyReadiness:
    """Full schema-contract check (columns/types/nullability/length/no-
    default/CHECK constraints -- see db.track_record_schema_contract), not
    just column-name presence. This is what --apply consults before
    deciding whether to report "Already applied," proceed with a fresh
    apply, or fail closed as corrupt."""
    conformance = verify_track_record_schema_contract(session)
    if not conformance.columns_exist:
        status = ApplyReadinessStatus.NOT_APPLIED
    elif conformance.conforms:
        status = ApplyReadinessStatus.FULLY_APPLIED
    else:
        status = ApplyReadinessStatus.CORRUPT
    return ApplyReadiness(status=status, conformance=conformance)


def _apply_and_verify_within_transaction(conn: Any) -> dict[str, Any]:
    """Execute migration 030's DDL on ``conn`` and verify, through that
    exact same connection, that the resulting schema fully conforms to
    the expected contract AND that zero rows have a non-NULL
    track_record_* value -- all before returning. Raises
    CompanyTrackRecordApplyPostconditionError on any mismatch; the
    caller's transaction (or savepoint) is then responsible for rolling
    back, exactly as it would on any other exception raised inside it.

    This is the shared core used by both apply_company_track_record_migration()
    (real engine.begin(), commits on success) and tests (a rolled-back
    savepoint on a connection that is never committed), so the exact same
    postcondition logic is exercised either way.
    """
    statements = company_track_record_migration_statements()
    for statement in statements:
        conn.execute(text(statement))

    conformance = verify_track_record_schema_contract(conn)
    if not conformance.conforms:
        raise CompanyTrackRecordApplyPostconditionError(
            "Refusing to commit migration 030: schema does not fully conform to "
            "the expected contract immediately after applying its DDL.\n"
            "  Violations:\n"
            + "\n".join(f"    - {v}" for v in conformance.describe_violations())
        )

    nonnull_row_count = company_track_record_nonnull_row_count(conn)
    if nonnull_row_count != 0:
        raise CompanyTrackRecordApplyPostconditionError(
            f"Refusing to commit migration 030: {nonnull_row_count} row(s) already "
            "have a non-NULL track_record_* value immediately after applying -- "
            "expected 0 (no wiring PR should exist yet)."
        )

    return {
        "statements_executed": len(statements),
        "migration": "030_company_track_record",
        "conforms": True,
        "nonnull_row_count": 0,
    }


def apply_company_track_record_migration(engine: Engine) -> dict[str, Any]:
    """Apply migration 030 and verify its postcondition, all inside one
    transaction: execute the DDL, then -- through that exact same
    connection -- run the full schema-contract check and confirm zero
    non-NULL rows. Any mismatch raises
    CompanyTrackRecordApplyPostconditionError, which (raised inside this
    ``with engine.begin()`` block) triggers an automatic ROLLBACK of the
    entire migration: success is only ever returned after the
    postcondition is fully confirmed, and nothing is committed otherwise.
    """
    with engine.begin() as conn:
        return _apply_and_verify_within_transaction(conn)


def company_track_record_nonnull_row_count(conn: Any) -> int:
    """Count of companies rows where any track_record_* column is
    non-NULL, read through the CALLER's own connection/transaction --
    this function opens no engine or transaction of its own, so it always
    observes the exact same snapshot as any other query already run on
    ``conn`` (e.g. verify_track_record_schema_contract(conn)). Before any
    wiring PR writes to these columns, this is expected to be 0; after a
    future wiring PR, a non-zero count is legitimate and does not by
    itself indicate a schema defect -- see build_track_record_audit_report's
    require_empty parameter for the policy distinction."""
    names = company_track_record_column_names()
    existing = _existing_column_names(conn, names)
    if not existing:
        return 0
    predicate = " OR ".join(f"{name} IS NOT NULL" for name in sorted(existing))
    return int(
        conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE {predicate}")
        ).scalar_one()
    )


def build_track_record_audit_report(
    conn: Any, *, require_empty: bool = False
) -> dict[str, Any]:
    """Build the full Class A audit report from a single, already-open
    connection -- one transaction, no separate Engine/Session snapshot.
    Both the schema-contract check and the non-NULL row count are read
    through this exact same ``conn``, so they can never observe two
    different points in time.

    ``nonnull_row_count`` is always reported informationally. It only
    contributes to a FAIL status when ``require_empty=True`` -- the
    post-migration/pre-wiring gate (confirms nothing has written to these
    columns yet, right after --apply). The default (require_empty=False)
    never fails merely because rows are legitimately non-empty after a
    future wiring PR has started writing real data -- that is expected,
    not a schema defect.
    """
    findings: list[str] = []

    static_ok, alter_violations = (
        company_track_record_migration_touches_only_companies()
    )
    if not static_ok:
        findings.append(
            f"migration SQL contains ALTER statements against non-companies tables: {alter_violations}"
        )

    conformance = verify_track_record_schema_contract(conn)
    findings.extend(conformance.describe_violations())

    nonnull_row_count: int | None = None
    if conformance.columns_exist:
        nonnull_row_count = company_track_record_nonnull_row_count(conn)
        if require_empty and nonnull_row_count != 0:
            findings.append(
                f"--require-empty: companies has {nonnull_row_count} row(s) with a "
                "non-NULL track_record_* value"
            )

    status = "PASS" if not findings else "FAIL"
    return {
        "schema_version": 2,
        "status": status,
        "migration": "030_company_track_record",
        "migration_sql_touches_only_companies": static_ok,
        "require_empty": require_empty,
        "columns_exist": conformance.columns_exist,
        "conforms": conformance.conforms,
        "missing_columns": list(conformance.missing_columns),
        "wrong_type_columns": list(conformance.wrong_type_columns),
        "wrong_nullability_columns": list(conformance.wrong_nullability_columns),
        "wrong_length_columns": list(conformance.wrong_length_columns),
        "columns_with_default": list(conformance.columns_with_default),
        "missing_check_constraints": list(conformance.missing_check_constraints),
        "wrong_check_constraints": list(conformance.wrong_check_constraints),
        "nonnull_row_count": nonnull_row_count,
        "findings": findings,
    }
