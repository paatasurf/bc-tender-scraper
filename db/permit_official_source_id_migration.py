"""Surrey official source identity schema foundation (migration 031) --
Class D helpers.

Applies one new, purely additive, nullable column plus one partial unique
index on the existing ``permits`` table. Never touches any other table,
never writes application data. Every existing row is left with
official_source_id NULL after apply.

No ORM mapping, importer, bridge, or scheduler wiring is added by this PR
-- db.models.Permit deliberately does not declare this column yet. That
wiring is a separate, later PR, applied only once this migration has
actually run in the target database, so application code is never deployed
ahead of the schema it depends on.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.permit_official_source_id_ddl import (
    COLUMN_NAME,
    INDEX_NAME,
    TABLE_NAME,
    permit_official_source_id_migration_statements,
)

__all__ = [
    "ApplyReadiness",
    "ApplyReadinessStatus",
    "PermitOfficialSourceIdSchemaCorruptError",
    "PermitOfficialSourceIdApplyPostconditionError",
    "permit_official_source_id_apply_readiness",
    "permit_official_source_id_before_stats",
    "permit_official_source_id_migration_pending",
    "apply_permit_official_source_id_migration",
    "permit_official_source_id_nonnull_row_count",
]


class PermitOfficialSourceIdSchemaCorruptError(RuntimeError):
    """Raised when the official_source_id column or its partial unique
    index exist in a state that does not match
    db/migrations/031_permit_official_source_id.sql -- e.g. the column
    exists without the index, or with the wrong type/nullability. Fail-
    closed: the operator must investigate manually. Nothing here attempts
    to silently repair a corrupt schema."""


class PermitOfficialSourceIdApplyPostconditionError(RuntimeError):
    """Raised by apply_permit_official_source_id_migration() when,
    immediately after executing migration 031's DDL -- through the exact
    same connection and transaction, before commit -- the resulting schema
    does not fully conform to the expected contract, or any row already
    has a non-NULL official_source_id. Raised inside the same
    ``with engine.begin()`` block that ran the DDL, so it triggers an
    automatic ROLLBACK of the entire migration."""


def _column_info(conn: Any) -> dict[str, Any] | None:
    row = (
        conn.execute(
            text(
                "SELECT data_type, character_maximum_length, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
            ),
            {"t": TABLE_NAME, "c": COLUMN_NAME},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


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


def _index_info(conn: Any) -> dict[str, Any] | None:
    """Real Postgres catalog introspection (pg_index / pg_get_expr / index
    key attributes) -- never a naive substring search over pg_indexes.indexdef
    text, which a differently-formatted-but-non-conforming definition could
    trivially fool."""
    row = (
        conn.execute(_INDEX_INFO_SQL, {"t": TABLE_NAME, "i": INDEX_NAME})
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _column_conforms(column: dict[str, Any]) -> bool:
    return (
        column["data_type"] == "character varying"
        and column["character_maximum_length"] == 100
        and column["is_nullable"] == "YES"
        and column["column_default"] is None
    )


_CAST_RE = re.compile(r"::[a-zA-Z_]+(?: [a-zA-Z_]+)*")
_EXPECTED_PREDICATE_CONJUNCTS = frozenset(
    {"official_source_id is not null", "official_source_id <> ''"}
)


def _normalize_predicate(raw: str | None) -> str | None:
    """Strip Postgres's deparsed type casts (::text, ::character varying,
    ...) and grouping parens, collapse whitespace, lowercase. Deliberately
    string-based rather than a full SQL-expression parse: the only shape
    this predicate is ever allowed to take is a two-term AND of the exact
    conjuncts below, so normalizing casts/parens/case/whitespace is enough
    to compare it safely without over-building a general SQL parser."""
    if raw is None:
        return None
    stripped = _CAST_RE.sub("", raw)
    stripped = stripped.replace("(", "").replace(")", "")
    return " ".join(stripped.lower().split())


def _predicate_conforms(raw_predicate: str | None) -> bool:
    normalized = _normalize_predicate(raw_predicate)
    if not normalized:
        return False
    conjuncts = [part.strip() for part in re.split(r"\band\b", normalized)]
    if len(conjuncts) != 2:
        return False
    return set(conjuncts) == _EXPECTED_PREDICATE_CONJUNCTS


def _index_conforms(index: dict[str, Any]) -> bool:
    return (
        bool(index.get("is_unique"))
        and list(index.get("key_columns") or []) == ["source", "official_source_id"]
        and _predicate_conforms(index.get("predicate"))
    )


def permit_official_source_id_before_stats(session_or_conn: Any) -> dict[str, Any]:
    """Lightweight column/index-existence-only snapshot -- used only as the
    dry-run-artifact staleness signal (has the schema state changed since
    the artifact was written?). Does NOT verify types/nullability -- see
    permit_official_source_id_apply_readiness() for the full contract check
    used to gate --apply."""
    column = _column_info(session_or_conn)
    index = _index_info(session_or_conn)
    statements = permit_official_source_id_migration_statements()
    return {
        "column_exists": column is not None,
        "index_exists": index is not None,
        "migration_pending": column is None or index is None,
        "statements_planned": len(statements),
    }


def permit_official_source_id_migration_pending(session: Session) -> bool:
    return bool(
        permit_official_source_id_before_stats(session).get("migration_pending")
    )


class ApplyReadinessStatus(str, Enum):
    NOT_APPLIED = "not_applied"  # neither column nor index exist -- safe to apply
    FULLY_APPLIED = "fully_applied"  # column + index fully match the contract
    CORRUPT = "corrupt"  # something exists but doesn't fully match the contract


@dataclass(frozen=True)
class ApplyReadiness:
    status: ApplyReadinessStatus
    column: dict[str, Any] | None
    index: dict[str, Any] | None

    @property
    def violations(self) -> list[str]:
        violations: list[str] = []
        if self.column is not None and not _column_conforms(self.column):
            violations.append(
                f"official_source_id column does not conform: {self.column}"
            )
        if self.index is not None and not _index_conforms(self.index):
            violations.append(f"{INDEX_NAME} index does not conform: {self.index}")
        if self.column is None and self.index is not None:
            violations.append("index exists but official_source_id column is missing")
        if self.column is not None and self.index is None:
            violations.append(
                "official_source_id column exists but the index is missing"
            )
        return violations


def permit_official_source_id_apply_readiness(session_or_conn: Any) -> ApplyReadiness:
    """Full schema-contract check (column type/nullability/length/no-default,
    plus index presence/uniqueness/partial predicate) -- not just existence.
    This is what --apply consults before deciding whether to report
    "Already applied," proceed, or fail closed as corrupt."""
    column = _column_info(session_or_conn)
    index = _index_info(session_or_conn)

    if column is None and index is None:
        status = ApplyReadinessStatus.NOT_APPLIED
    elif (
        column is not None
        and index is not None
        and _column_conforms(column)
        and _index_conforms(index)
    ):
        status = ApplyReadinessStatus.FULLY_APPLIED
    else:
        status = ApplyReadinessStatus.CORRUPT
    return ApplyReadiness(status=status, column=column, index=index)


def permit_official_source_id_nonnull_row_count(conn: Any) -> int:
    """Count of permits rows where official_source_id is non-NULL, read
    through the CALLER's own connection/transaction. Before this migration
    has ever been populated by a future bridge PR, this is expected to be
    0 immediately after --apply."""
    column = _column_info(conn)
    if column is None:
        return 0
    return int(
        conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE {COLUMN_NAME} IS NOT NULL")
        ).scalar_one()
    )


def _apply_and_verify_within_transaction(conn: Any) -> dict[str, Any]:
    statements = permit_official_source_id_migration_statements()
    for statement in statements:
        conn.execute(text(statement))

    readiness = permit_official_source_id_apply_readiness(conn)
    if readiness.status is not ApplyReadinessStatus.FULLY_APPLIED:
        raise PermitOfficialSourceIdApplyPostconditionError(
            "Refusing to commit migration 031: schema does not fully conform to "
            "the expected contract immediately after applying its DDL.\n"
            "  Violations:\n" + "\n".join(f"    - {v}" for v in readiness.violations)
        )

    nonnull_row_count = permit_official_source_id_nonnull_row_count(conn)
    if nonnull_row_count != 0:
        raise PermitOfficialSourceIdApplyPostconditionError(
            f"Refusing to commit migration 031: {nonnull_row_count} row(s) already "
            "have a non-NULL official_source_id immediately after applying -- "
            "expected 0 (no bridge/wiring PR should exist yet)."
        )

    return {
        "statements_executed": len(statements),
        "migration": "031_permit_official_source_id",
        "conforms": True,
        "nonnull_row_count": 0,
    }


def apply_permit_official_source_id_migration(engine: Engine) -> dict[str, Any]:
    """Apply migration 031 and verify its postcondition, all inside one
    transaction: execute the DDL, then -- through that exact same
    connection -- run the full schema-contract check and confirm zero
    non-NULL rows. Any mismatch raises
    PermitOfficialSourceIdApplyPostconditionError, which (raised inside
    this ``with engine.begin()`` block) triggers an automatic ROLLBACK of
    the entire migration."""
    with engine.begin() as conn:
        return _apply_and_verify_within_transaction(conn)
