"""DDL helpers for KG observation spine (migration 025)."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_025 = Path(__file__).resolve().parent / "migrations" / "025_kg_observations.sql"


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


def kg_observation_migration_statements() -> list[str]:
    """Return SQL statements for migration 025 (Observation spine)."""
    return _statements_from_path(_MIGRATION_025)
