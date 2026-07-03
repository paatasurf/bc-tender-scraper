"""DDL helpers for Google enrichment schema (migration 013)."""

from __future__ import annotations

from pathlib import Path

_MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / "013_google_enrichment.sql"

COMPANY_GOOGLE_COLUMN_NAMES: tuple[str, ...] = (
    "google_place_id",
    "google_business_category",
    "google_maps_url",
    "google_business_status",
    "google_website",
    "google_last_updated",
    "google_last_seen",
    "google_match_confidence",
    "google_enrichment_status",
    "google_query_used",
    "website",
    "google_lat",
    "google_lng",
)


def google_enrichment_migration_statements() -> list[str]:
    """Return individual SQL statements from migration 013."""
    raw = _MIGRATION_PATH.read_text(encoding="utf-8")
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


def google_enrichment_migration_sql() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")
