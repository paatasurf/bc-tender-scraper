"""DDL helpers for construction tier schema (migration 017)."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_017 = Path(__file__).resolve().parent / "migrations" / "017_construction_tier.sql"
_MIGRATION_018 = Path(__file__).resolve().parent / "migrations" / "018_construction_score.sql"
_MIGRATION_020 = Path(__file__).resolve().parent / "migrations" / "020_company_score_history.sql"


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


def construction_tier_migration_statements() -> list[str]:
    statements = _statements_from_path(_MIGRATION_017)
    statements.extend(_statements_from_path(_MIGRATION_018))
    statements.extend(_statements_from_path(_MIGRATION_020))
    return statements
