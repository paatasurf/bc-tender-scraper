"""DDL helpers for registry verification schema (migration 016)."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_016 = Path(__file__).resolve().parent / "migrations" / "016_registry_verification.sql"
_MIGRATION_019 = Path(__file__).resolve().parent / "migrations" / "019_orgbook_reference.sql"


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


def registry_verification_migration_statements() -> list[str]:
    """Return SQL statements for registry verification schema."""
    statements = _statements_from_path(_MIGRATION_016)
    statements.extend(_statements_from_path(_MIGRATION_019))
    return statements
