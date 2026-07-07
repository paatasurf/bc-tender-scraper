"""Shared permit scraper persistence and CSV-writing utilities."""

from __future__ import annotations

import csv
from typing import Any


def write_permit_csv(
    records: list[dict[str, str]],
    csv_path: str,
    fieldnames: list[str],
    *,
    append: bool,
) -> None:
    """Write permit records to CSV. Overwrites on full load; appends on incremental."""
    if not records:
        return
    mode = "a" if append else "w"
    write_header = not append
    with open(csv_path, mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def persist_permits_to_db(
    records: list[dict[str, str]],
    *,
    source: str,
    full_refresh: bool,
) -> int:
    """Persist permit records to the database. Returns count of persisted rows."""
    from db.connection import get_session, init_db
    from db.permit_import import upsert_city_permits

    init_db()
    session = get_session()
    try:
        return upsert_city_permits(
            session,
            records,
            source=source,
            full_refresh=full_refresh,
        )
    finally:
        session.close()


def scrape_and_persist_permits(
    records: list[dict[str, str]],
    *,
    source: str,
    city: str,
    csv_path: str,
    fieldnames: list[str],
    days: int | None,
    persist: bool,
) -> dict[str, Any]:
    """Common scrape-and-persist logic for all permit scrapers.

    Writes CSV, optionally persists to DB, prints summary, returns result dict.
    """
    incremental = days is not None and days > 0
    write_permit_csv(records, csv_path, fieldnames, append=incremental)

    result: dict[str, Any] = {
        "source": source,
        "city": city,
        "mode": "incremental" if incremental else "full",
        "days": days,
        "permits_scraped": len(records),
        "csv_path": csv_path,
    }

    if persist and records:
        result["permits_persisted"] = persist_permits_to_db(
            records,
            source=source,
            full_refresh=not incremental,
        )
    else:
        result["permits_persisted"] = 0

    print(
        f"[{city} Permits] Saved {len(records)} permits to {csv_path}"
        f" ({result.get('permits_persisted', 0)} persisted)"
    )
    return result
