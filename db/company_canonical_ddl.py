"""DDL helpers for company canonical merge schema (migration 014)."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_014 = Path(__file__).resolve().parent / "migrations" / "014_company_canonical_merge.sql"
_MIGRATION_015 = Path(__file__).resolve().parent / "migrations" / "015_probable_person_entity_role.sql"


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


def company_canonical_migration_statements() -> list[str]:
    """Return SQL statements from migrations 014 + 015."""
    statements: list[str] = []
    statements.extend(_statements_from_path(_MIGRATION_014))
    statements.extend(_statements_from_path(_MIGRATION_015))
    return statements
