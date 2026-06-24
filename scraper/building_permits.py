"""Vancouver issued building permits — City Open Data (Opendatasoft Explore v2.1)."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from scraper.config import (
    BUILDING_PERMITS_CSV,
    VANCOUVER_CITY,
    VANCOUVER_PERMITS_API,
    VANCOUVER_SOURCE,
)
from scraper.utils import clean_text, create_session, polite_api_get

PAGE_SIZE = 100
DEFAULT_INCREMENTAL_DAYS = 14
FIELDNAMES = [
    "external_id",
    "address",
    "permit_type",
    "project_value",
    "applicant",
    "application_date",
    "issue_date",
    "contractor",
    "local_area",
    "description",
    "source",
    "city",
]


def _parse_date(raw: str | None) -> str:
    value = clean_text(raw)
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return value[:10] if len(value) >= 10 else value


def _format_record(record: dict[str, Any]) -> dict[str, str]:
    value = record.get("projectvalue")
    project_value = "" if value is None else str(value)
    external_id = clean_text(record.get("permitnumber"))
    address = clean_text(record.get("address"))

    return {
        "external_id": external_id,
        "address": address or external_id,
        "permit_type": clean_text(record.get("typeofwork")),
        "project_value": project_value,
        "applicant": clean_text(record.get("applicant")),
        "application_date": _parse_date(record.get("permitnumbercreateddate")),
        "issue_date": _parse_date(record.get("issuedate")),
        "contractor": clean_text(record.get("buildingcontractor")),
        "local_area": clean_text(record.get("geolocalarea")),
        "description": clean_text(record.get("projectdescription")),
        "source": VANCOUVER_SOURCE,
        "city": VANCOUVER_CITY,
    }


def _date_cutoff(days: int) -> str:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    return cutoff.isoformat()


def _build_incremental_where(days: int) -> str:
    since = _date_cutoff(days)
    return (
        f"issuedate >= '{since}' OR permitnumbercreateddate >= '{since}'"
    )


def _iter_api_pages(
    session,
    *,
    where: str,
    order_by: str = "-issuedate",
) -> Iterator[list[dict[str, str]]]:
    offset = 0
    total_count: int | None = None

    while True:
        response = polite_api_get(
            session,
            VANCOUVER_PERMITS_API,
            params={
                "limit": PAGE_SIZE,
                "offset": offset,
                "where": where,
                "order_by": order_by,
            },
        )
        response.raise_for_status()
        payload = response.json()

        if total_count is None:
            total_count = int(payload.get("total_count") or 0)
            print(f"[Vancouver Permits] Query `{where}`: {total_count} records")

        results = payload.get("results") or []
        if not results:
            break

        rows = [_format_record(record) for record in results if record.get("permitnumber")]
        if rows:
            yield rows

        offset += len(results)
        if total_count is not None and offset >= total_count:
            break


def _iter_year_pages(session, year: int) -> Iterator[list[dict[str, str]]]:
    yield from _iter_api_pages(
        session,
        where=f"issueyear={year}",
        order_by="-issuedate",
    )


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
    session = create_session()
    if days is None or days <= 0:
        years = _discover_years(session)
        print(
            "[Vancouver Permits] Full history by issue year: "
            f"{', '.join(str(year) for year in years)}"
        )
        for year in years:
            for page in _iter_year_pages(session, year):
                yield from page
        return

    where = _build_incremental_where(days)
    print(f"[Vancouver Permits] Incremental window: last {days} days")
    for page in _iter_api_pages(session, where=where):
        yield from page


def _write_csv(records: list[dict[str, str]], *, append: bool) -> None:
    if not records:
        return
    mode = "a" if append else "w"
    write_header = not append
    with open(BUILDING_PERMITS_CSV, mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def scrape_vancouver_permits(*, days: int | None = None, persist: bool = True) -> dict[str, Any]:
    """Scrape Vancouver permits. Full history when days is None; incremental when days > 0."""
    records = list(iter_vancouver_permits(days=days))
    incremental = days is not None and days > 0
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
        from db.connection import get_session, init_db
        from db.permit_import import upsert_city_permits

        init_db()
        session = get_session()
        try:
            result["permits_persisted"] = upsert_city_permits(
                session,
                records,
                source=VANCOUVER_SOURCE,
                full_refresh=not incremental,
            )
        finally:
            session.close()
    else:
        result["permits_persisted"] = 0

    print(
        f"[Vancouver Permits] Saved {len(records)} permits to {BUILDING_PERMITS_CSV}"
        f" ({result.get('permits_persisted', 0)} persisted)"
    )
    return result


def scrape_building_permits() -> int:
    """Backward-compatible entry point: full CSV scrape without DB persist."""
    result = scrape_vancouver_permits(days=None, persist=False)
    return int(result["permits_scraped"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Vancouver issued building permits")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Incremental window in days (omit for full historical load)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Write CSV only; do not upsert into PostgreSQL",
    )
    args = parser.parse_args()
    scrape_vancouver_permits(days=args.days, persist=not args.no_persist)


if __name__ == "__main__":
    main()
