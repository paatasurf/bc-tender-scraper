"""Runtime DDL helpers for permit company resolution columns (migration 027)."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_027 = Path(__file__).resolve().parent / "migrations" / "027_permit_company_columns.sql"


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


def permit_company_migration_statements() -> list[str]:
    """Return SQL statements from migration 027."""
    return _statements_from_path(_MIGRATION_027)
