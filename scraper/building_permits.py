from __future__ import annotations

from datetime import datetime

from scraper.config import BUILDING_PERMITS_CSV, VANCOUVER_PERMITS_API
from scraper.utils import clean_text, create_session, polite_api_get, save_csv_rows

PAGE_SIZE = 100
FIELDNAMES = [
    "address",
    "permit_type",
    "project_value",
    "applicant",
    "issue_date",
    "description",
]


def _format_record(record: dict) -> dict[str, str]:
    issue_date = record.get("issuedate") or ""
    if issue_date:
        try:
            issue_date = datetime.fromisoformat(issue_date).date().isoformat()
        except ValueError:
            issue_date = clean_text(str(issue_date))

    value = record.get("projectvalue")
    project_value = "" if value is None else str(value)

    return {
        "address": clean_text(record.get("address")),
        "permit_type": clean_text(record.get("typeofwork")),
        "project_value": project_value,
        "applicant": clean_text(record.get("applicant")),
        "issue_date": issue_date,
        "description": clean_text(record.get("projectdescription")),
    }


def _fetch_year(session, year: int) -> list[dict[str, str]]:
    permits: list[dict[str, str]] = []
    offset = 0
    total_count = None

    while True:
        response = polite_api_get(
            session,
            VANCOUVER_PERMITS_API,
            params={"limit": PAGE_SIZE, "offset": offset, "where": f"issueyear={year}"},
        )
        response.raise_for_status()
        payload = response.json()

        if total_count is None:
            total_count = payload.get("total_count", 0)
            print(f"[Building Permits] Year {year}: {total_count} records")

        results = payload.get("results", [])
        if not results:
            break

        for record in results:
            permits.append(_format_record(record))

        offset += len(results)
        if offset >= total_count:
            break

    return permits


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


def scrape_building_permits() -> list[dict[str, str]]:
    session = create_session()
    permits: list[dict[str, str]] = []

    print("[Building Permits] Starting Vancouver issued building permits scrape")
    years = _discover_years(session)
    print(f"[Building Permits] Fetching years: {', '.join(str(year) for year in years)}")

    for year in years:
        permits.extend(_fetch_year(session, year))
        print(f"[Building Permits] Running total: {len(permits)} permits")

    save_csv_rows(permits, BUILDING_PERMITS_CSV, FIELDNAMES)
    print(f"[Building Permits] Saved {len(permits)} permits to {BUILDING_PERMITS_CSV}")
    return permits
