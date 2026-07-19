"""DDL helpers for the Company track-record schema foundation (migration 030).

Mirrors db.classification_claims_ddl's structure (statement parsing +
canonical digest), adapted for a single additive ALTER-TABLE migration
instead of new-table CREATE statements.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_MIGRATION_030 = (
    Path(__file__).resolve().parent / "migrations" / "030_company_track_record.sql"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _statements_from_path(path: Path) -> list[str]:
    """Split a .sql file into top-level statements on a trailing ';',
    skipping '--' line comments -- except while inside a dollar-quoted
    ($$ ... $$) block, where comment lines are preserved verbatim (they
    are part of the PL/pgSQL body) and an internal ';' must never be
    treated as a statement terminator. Each '$$' token toggles block
    state; migration 030's DO $$ ... END $$; block is therefore parsed
    as exactly one statement, not split at its internal ADD CONSTRAINT/
    END IF semicolons."""
    raw = path.read_text(encoding="utf-8")
    parts: list[str] = []
    buffer: list[str] = []
    in_dollar_block = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not in_dollar_block and stripped.startswith("--"):
            continue
        buffer.append(line)
        if line.count("$$") % 2 == 1:
            in_dollar_block = not in_dollar_block
        if not in_dollar_block and stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                parts.append(statement)
            buffer = []
    if buffer:
        statement = "\n".join(buffer).strip()
        if statement:
            parts.append(statement)
    return parts


def company_track_record_migration_statements() -> list[str]:
    """Return SQL statements for migration 030: 4 ADD COLUMN statements
    followed by 1 combined DO $$ ... END $$; block (3 idempotency-guarded
    ADD CONSTRAINT statements inside it)."""
    return _statements_from_path(_MIGRATION_030)


def company_track_record_column_names() -> list[str]:
    return [
        "track_record_score",
        "track_record_json",
        "track_record_at",
        "track_record_version",
    ]


def company_track_record_check_constraint_names() -> list[str]:
    return [
        "ck_companies_track_record_score_range",
        "ck_companies_track_record_version_not_empty",
        "ck_companies_track_record_state_coherent",
    ]


def _canonical_digest(statements: list[str]) -> str:
    canonical = json.dumps(statements, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def company_track_record_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical (comment-stripped, parsed)
    DDL statement list in 030_company_track_record.sql. Changes whenever
    the DDL changes -- used to detect a stale dry-run artifact before
    --apply (see scripts/run_company_track_record_migration.py)."""
    return _canonical_digest(company_track_record_migration_statements())


def is_valid_ddl_digest(value: object) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def company_track_record_migration_touches_only_companies() -> tuple[bool, list[str]]:
    """Static check (no DB): the migration file must only ever ALTER TABLE
    companies -- never any other table. Returns (ok, violating_table_names)."""
    source = _MIGRATION_030.read_text(encoding="utf-8")
    targets = re.findall(r"ALTER TABLE\s+(\w+)", source, flags=re.IGNORECASE)
    violations = sorted({t for t in targets if t != "companies"})
    return (len(violations) == 0, violations)
