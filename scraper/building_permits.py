from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from scraper.config import BUILDING_PERMITS_CSV, VANCOUVER_PERMITS_API
from scraper.permit_persist import persist_permits_to_db
from scraper.utils import clean_text, create_session, polite_api_get

PAGE_SIZE = 100
VANCOUVER_SOURCE = "vancouver"
VANCOUVER_CITY = "Vancouver"
DEFAULT_DAILY_LOOKBACK_DAYS = 30

FIELDNAMES = [
    "external_id",
    "address",
    "permit_type",
    "project_value",
    "applicant",
    "issue_date",
    "application_date",
    "description",
    "contractor",
    "local_area",
    "source",
    "city",
]


def _parse_date(raw: str | None) -> str:
    value = clean_text(raw)
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("/", "-")[:10]).date().isoformat()
    except ValueError:
        return value[:10] if len(value) >= 10 else value


def _format_vancouver_record(record: dict[str, Any]) -> dict[str, str]:
    value = record.get("projectvalue")
    project_value = "" if value is None else str(value)

    return {
        "external_id": clean_text(record.get("permitnumber")),
        "address": clean_text(record.get("address")),
        "permit_type": clean_text(record.get("typeofwork")),
        "project_value": project_value,
        "applicant": clean_text(record.get("applicant")),
        "issue_date": _parse_date(record.get("issuedate")),
        "application_date": _parse_date(record.get("permitnumbercreateddate")),
        "description": clean_text(record.get("projectdescription")),
        "contractor": clean_text(record.get("buildingcontractor")),
        "local_area": clean_text(record.get("geolocalarea")),
        "source": VANCOUVER_SOURCE,
        "city": VANCOUVER_CITY,
    }


def _date_cutoff(days: int) -> str:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    return cutoff.isoformat()


def _build_incremental_where(days: int) -> str:
    cutoff = _date_cutoff(days)
    return f'permitnumbercreateddate > "{cutoff}" OR issuedate > "{cutoff}"'


def _iter_query_pages(session, *, where: str) -> Iterator[list[dict[str, str]]]:
    offset = 0
    total_count = None

    while True:
        response = polite_api_get(
            session,
            VANCOUVER_PERMITS_API,
            params={"limit": PAGE_SIZE, "offset": offset, "where": where},
        )
        response.raise_for_status()
        payload = response.json()

        if total_count is None:
            total_count = payload.get("total_count", 0)
            print(f"[Vancouver Permits] Filter {where!r}: {total_count} records")

        results = payload.get("results", [])
        if not results:
            break

        yield [_format_vancouver_record(record) for record in results]

        offset += len(results)
        if offset >= total_count:
            break


def _iter_year_pages(session, year: int) -> Iterator[list[dict[str, str]]]:
    yield from _iter_query_pages(session, where=f"issueyear={year}")


def _discover_years(session) -> list[int]:
    years: list[int] = []
    for year in range(1990, datetime.now().year + 1):
        response = polite_api_get(
            session,
            VANCOUVER_PERMITS_API,
            params={"limit": 1, "where": f"issueyear={year}"},
        )
        response.raise_for_status()
        count = response.json().get("total_count", 0)
        if count:
            years.append(year)
    return years


def iter_vancouver_permits(*, days: int | None = None) -> Iterator[dict[str, str]]:
    """Yield Vancouver permit rows. Full history when days is None; incremental when days > 0."""
    session = create_session()
    incremental = days is not None and days > 0

    if incremental:
        where = _build_incremental_where(days)
        for page in _iter_query_pages(session, where=where):
            yield from page
        return

    years = _discover_years(session)
    print(f"[Vancouver Permits] Fetching years: {', '.join(str(year) for year in years)}")
    for year in years:
        for page in _iter_year_pages(session, year):
            yield from page


def _read_existing_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) != FIELDNAMES:
            print(
                "[Vancouver Permits] Existing CSV header order differs from canonical columns; "
                "skipping merge to avoid column misalignment"
            )
            return []
        return [{field: row.get(field, "") for field in FIELDNAMES} for row in reader]


def _write_csv(records: list[dict[str, str]], *, append: bool) -> None:
    csv_path = Path(BUILDING_PERMITS_CSV)
    merged: dict[str, dict[str, str]] = {}

    if append:
        for row in _read_existing_rows(csv_path):
            external_id = row.get("external_id", "")
            if external_id:
                merged[external_id] = row

    for row in records:
        external_id = row.get("external_id", "")
        if external_id:
            merged[external_id] = row

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        if merged:
            writer.writerows(merged.values())
            handle.flush()


def scrape_vancouver_permits(*, days: int | None = None, persist: bool = True) -> dict[str, Any]:
    """Scrape Vancouver issued building permits with application metadata."""
    incremental = days is not None and days > 0
    records = list(iter_vancouver_permits(days=days))
    _write_csv(records, append=incremental)

    result: dict[str, Any] = {
        "source": VANCOUVER_SOURCE,
        "city": VANCOUVER_CITY,
        "mode": "incremental" if incremental else "full",
        "days": days,
        "permits_scraped": len(records),
        "csv_path": BUILDING_PERMITS_CSV,
    }

    if persist and records:
        result["permits_persisted"] = persist_permits_to_db(
            records,
            source=VANCOUVER_SOURCE,
            full_refresh=False,
        )
    else:
        result["permits_persisted"] = 0

    print(
        f"[Vancouver Permits] Saved {len(records)} permits to {BUILDING_PERMITS_CSV}"
        f" ({result.get('permits_persisted', 0)} persisted)"
    )
    return result


def scrape_building_permits() -> int:
    """Legacy entrypoint: full history scrape to CSV only (no DB persist)."""
    return scrape_vancouver_permits(days=None, persist=False)["permits_scraped"]
