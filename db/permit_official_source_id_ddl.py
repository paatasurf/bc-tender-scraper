"""Runtime DDL helpers for the Surrey official source identity schema
foundation (migration 031). See db/migrations/031_permit_official_source_id.sql
and its paired rollback -- this module only parses those files into
individual statements and offers a couple of static content checks; it does
not apply anything and is not wired into db.connection._run_migrations()."""

from __future__ import annotations

import hashlib
import json
import re

from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_031 = _MIGRATIONS_DIR / "031_permit_official_source_id.sql"
_MIGRATION_031_ROLLBACK = _MIGRATIONS_DIR / "031_permit_official_source_id_rollback.sql"

_ALTER_TABLE_RE = re.compile(r"ALTER\s+TABLE\s+(\S+)", re.IGNORECASE)
_INDEX_ON_RE = re.compile(
    r"INDEX\s+IF\s+NOT\s+EXISTS\s+\S+\s+ON\s+(\S+)", re.IGNORECASE
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INDEX_NAME = "ux_permits_source_official_source_id"
TABLE_NAME = "permits"
COLUMN_NAME = "official_source_id"


def _statements_from_path(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parts: list[str] = []
    buffer: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                parts.append(statement)
            buffer = []
    if buffer:
        statement = "\n".join(buffer).strip()
        if statement:
            parts.append(statement)
    return parts


def permit_official_source_id_migration_statements() -> list[str]:
    """Return the forward migration 031 SQL statements."""
    return _statements_from_path(_MIGRATION_031)


def permit_official_source_id_rollback_statements() -> list[str]:
    """Return migration 031's paired rollback SQL statements."""
    return _statements_from_path(_MIGRATION_031_ROLLBACK)


def permit_official_source_id_migration_touches_only_permits() -> (
    tuple[bool, list[str]]
):
    """Static check: every ALTER TABLE / CREATE INDEX ... ON in the forward
    migration targets ``permits`` only. Returns (ok, offending_table_names)."""
    offenders: list[str] = []
    for statement in permit_official_source_id_migration_statements():
        for pattern in (_ALTER_TABLE_RE, _INDEX_ON_RE):
            match = pattern.search(statement)
            if match and match.group(1).strip('"') != "permits":
                offenders.append(match.group(1))
    return not offenders, offenders


def permit_official_source_id_migration_column_names() -> list[str]:
    """The single new nullable column this migration adds."""
    return ["official_source_id"]


def _canonical_digest(statements: list[str]) -> str:
    canonical = json.dumps(statements, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def permit_official_source_id_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical (comment-stripped, parsed)
    forward-migration statement list. Changes whenever the DDL changes --
    used to detect a stale dry-run artifact before --apply."""
    return _canonical_digest(permit_official_source_id_migration_statements())


def is_valid_ddl_digest(value: object) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))
