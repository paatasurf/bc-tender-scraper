"""DDL helpers for Integration Connections (migration 031, AI-Workforce PR1).

Mirrors db/registry_gateway_ddl.py's exact statement-splitting pattern.
This module only reads and splits the SQL file into executable
statements -- it does not apply anything, and is not imported by
db.connection._run_migrations()/init_db(). Applying the migration is the
sole responsibility of an explicit operator script (not yet written in
this PR -- see the PR description: --apply is intentionally not wired up
until a later PR, once this schema has been reviewed).
"""

from __future__ import annotations

from pathlib import Path

_MIGRATION_031 = (
    Path(__file__).resolve().parent / "migrations" / "031_integration_connections.sql"
)


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


def integration_connections_migration_statements() -> list[str]:
    """Return SQL statements for migration 031 (Integration Connections)."""
    return _statements_from_path(_MIGRATION_031)
