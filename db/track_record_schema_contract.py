"""Single source of truth for the Company track-record schema contract
(migration 030) -- the exact columns (name, type, nullability, absence of
a default) and CHECK constraints (name AND expression) expected on the
``companies`` table.

Used by both ``db.track_record_migration`` (the "is this already fully and
correctly applied" gate before ``--apply`` reports "Already applied") and
``scripts/run_company_track_record_schema_audit.py`` (the Class A audit),
so the two checks can never silently drift apart from each other or from
``db/migrations/030_company_track_record.sql``.

A matching *name* is never treated as sufficient on its own for a CHECK
constraint -- the actual expression is introspected via PostgreSQL's own
canonicalizing catalog function (``pg_get_constraintdef``) and compared
against the expected value, captured from a real local-Postgres
application of migration 030 (rolled back afterwards -- see
tests/unit/test_track_record_schema_contract.py for the equivalent live
capture used as a regression check).

Read-only introspection only -- no DDL, no writes. Mirrors the structure
of db.classification_claims_schema_contract, scoped down to one table with
no primary key, unique constraint, index, or foreign key of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


def _normalize_sql_text(raw: str | None) -> str | None:
    """Deterministic normalization for comparing PostgreSQL-deparsed CHECK
    expressions: trim, collapse whitespace runs to a single space,
    lowercase. PostgreSQL's own deparser (pg_get_constraintdef) already
    canonicalizes casts/parens/literal formatting for a given expression
    tree, so this only needs to absorb incidental whitespace/case
    differences, not do any SQL-aware rewriting."""
    if raw is None:
        return None
    return " ".join(raw.strip().lower().split())


@dataclass(frozen=True)
class ColumnContract:
    name: str
    data_type: str  # exact information_schema.columns.data_type string
    is_nullable: bool
    max_length: int | None = None  # character_maximum_length, when applicable


@dataclass(frozen=True)
class CheckConstraintContract:
    name: str
    expression: (
        str  # expected pg_get_constraintdef() output; compared via _normalize_sql_text
    )


TABLE_NAME = "companies"

TRACK_RECORD_COLUMNS: tuple[ColumnContract, ...] = (
    ColumnContract("track_record_score", "integer", True),
    ColumnContract("track_record_json", "jsonb", True),
    ColumnContract("track_record_at", "timestamp with time zone", True),
    ColumnContract("track_record_version", "character varying", True, max_length=64),
)

# Captured verbatim from a real local-Postgres application of migration 030
# (applied inside a rolled-back transaction -- never persisted). See the
# module docstring.
TRACK_RECORD_CHECK_CONSTRAINTS: tuple[CheckConstraintContract, ...] = (
    CheckConstraintContract(
        "ck_companies_track_record_score_range",
        "CHECK (((track_record_score IS NULL) OR ((track_record_score >= 0) "
        "AND (track_record_score <= 100))))",
    ),
    CheckConstraintContract(
        "ck_companies_track_record_version_not_empty",
        "CHECK (((track_record_version IS NULL) OR ((track_record_version)::text <> ''::text)))",
    ),
    CheckConstraintContract(
        "ck_companies_track_record_state_coherent",
        "CHECK ((((track_record_json IS NULL) AND (track_record_at IS NULL) "
        "AND (track_record_version IS NULL) AND (track_record_score IS NULL)) "
        "OR ((track_record_json IS NOT NULL) AND (track_record_at IS NOT NULL) "
        "AND (track_record_version IS NOT NULL))))",
    ),
)


@dataclass(frozen=True)
class TrackRecordConformance:
    columns_exist: bool  # True iff at least one of the 4 columns exists
    missing_columns: tuple[str, ...] = ()
    wrong_type_columns: tuple[str, ...] = ()
    wrong_nullability_columns: tuple[str, ...] = ()
    wrong_length_columns: tuple[str, ...] = ()
    columns_with_default: tuple[str, ...] = ()  # "no defaults" requirement
    missing_check_constraints: tuple[str, ...] = ()
    wrong_check_constraints: tuple[
        str, ...
    ] = ()  # name exists, expression does not match

    @property
    def conforms(self) -> bool:
        return (
            self.columns_exist
            and not self.missing_columns
            and not self.wrong_type_columns
            and not self.wrong_nullability_columns
            and not self.wrong_length_columns
            and not self.columns_with_default
            and not self.missing_check_constraints
            and not self.wrong_check_constraints
        )

    def describe_violations(self) -> list[str]:
        lines: list[str] = []
        if not self.columns_exist:
            lines.append(f"{TABLE_NAME}: no track_record_* columns exist")
            return lines
        if self.missing_columns:
            lines.append(f"{TABLE_NAME}: missing columns {list(self.missing_columns)}")
        if self.wrong_type_columns:
            lines.append(
                f"{TABLE_NAME}: columns with wrong data type {list(self.wrong_type_columns)}"
            )
        if self.wrong_nullability_columns:
            lines.append(
                f"{TABLE_NAME}: columns with wrong nullability {list(self.wrong_nullability_columns)}"
            )
        if self.wrong_length_columns:
            lines.append(
                f"{TABLE_NAME}: columns with wrong max length {list(self.wrong_length_columns)}"
            )
        if self.columns_with_default:
            lines.append(
                f"{TABLE_NAME}: columns unexpectedly have a default {list(self.columns_with_default)}"
            )
        if self.missing_check_constraints:
            lines.append(
                f"{TABLE_NAME}: missing CHECK constraints {list(self.missing_check_constraints)}"
            )
        if self.wrong_check_constraints:
            lines.append(
                f"{TABLE_NAME}: CHECK constraints present but with a different expression than "
                f"expected {list(self.wrong_check_constraints)}"
            )
        return lines


def verify_track_record_schema_contract(conn: Any) -> TrackRecordConformance:
    """Read-only. Accepts a SQLAlchemy Connection or Session -- anything
    with an ``.execute()`` compatible with ``text()`` statements."""
    col_rows = conn.execute(
        text("""
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
              AND column_name = ANY(:names)
            """),
        {"t": TABLE_NAME, "names": [c.name for c in TRACK_RECORD_COLUMNS]},
    ).all()
    actual_cols = {
        r[0]: {
            "data_type": r[1],
            "is_nullable": r[2] == "YES",
            "has_default": r[3] is not None,
            "max_length": r[4],
        }
        for r in col_rows
    }

    if not actual_cols:
        return TrackRecordConformance(
            columns_exist=False,
            missing_columns=tuple(c.name for c in TRACK_RECORD_COLUMNS),
        )

    missing_columns = tuple(
        c.name for c in TRACK_RECORD_COLUMNS if c.name not in actual_cols
    )
    wrong_type = tuple(
        c.name
        for c in TRACK_RECORD_COLUMNS
        if c.name in actual_cols and actual_cols[c.name]["data_type"] != c.data_type
    )
    wrong_null = tuple(
        c.name
        for c in TRACK_RECORD_COLUMNS
        if c.name in actual_cols and actual_cols[c.name]["is_nullable"] != c.is_nullable
    )
    wrong_length = tuple(
        c.name
        for c in TRACK_RECORD_COLUMNS
        if c.name in actual_cols
        and c.max_length is not None
        and actual_cols[c.name]["max_length"] != c.max_length
    )
    columns_with_default = tuple(
        c.name
        for c in TRACK_RECORD_COLUMNS
        if c.name in actual_cols and actual_cols[c.name]["has_default"]
    )

    check_rows = conn.execute(
        text("""
            SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = (:t)::regclass AND contype = 'c'
              AND conname = ANY(:names)
            """),
        {
            "t": TABLE_NAME,
            "names": [c.name for c in TRACK_RECORD_CHECK_CONSTRAINTS],
        },
    ).all()
    actual_checks = {r[0]: r[1] for r in check_rows}
    missing_checks = tuple(
        c.name for c in TRACK_RECORD_CHECK_CONSTRAINTS if c.name not in actual_checks
    )
    wrong_checks = tuple(
        c.name
        for c in TRACK_RECORD_CHECK_CONSTRAINTS
        if c.name in actual_checks
        and _normalize_sql_text(actual_checks[c.name])
        != _normalize_sql_text(c.expression)
    )

    return TrackRecordConformance(
        columns_exist=True,
        missing_columns=missing_columns,
        wrong_type_columns=wrong_type,
        wrong_nullability_columns=wrong_null,
        wrong_length_columns=wrong_length,
        columns_with_default=columns_with_default,
        missing_check_constraints=missing_checks,
        wrong_check_constraints=wrong_checks,
    )
