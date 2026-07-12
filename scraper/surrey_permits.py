"""Surrey issued building permits — ArcGIS Open Data (Feature 010)."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from scraper.config import (
    SURREY_CITY,
    SURREY_PERMITS_API,
    SURREY_PERMITS_CSV,
    SURREY_SOURCE,
)
from scraper.permit_persist import scrape_and_persist_permits
from scraper.utils import clean_text, create_session, polite_api_get

DEFAULT_PAGE_SIZE = 500
OUT_FIELDS = (
    "PermitNumber,ProjectAddress,WorkDescription,SubDescription,"
    "IssuedDate,ValueOfConstruction,ApplicantOrganization"
)
FIELDNAMES = [
    "external_id",
    "address",
    "permit_type",
    "project_value",
    "applicant",
    "issue_date",
    "description",
    "source",
    "city",
]


def _parse_issue_date(raw: str | None) -> str:
    value = clean_text(raw)
    if not value:
        return ""
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    if len(value) >= 10 and value[4] == "-":
        return value[:10]
    return value


def _project_address(attrs: dict[str, Any]) -> str:
    return clean_text(attrs.get("ProjectAddress")) or clean_text(attrs.get("Address"))


def _format_record(attrs: dict[str, Any]) -> dict[str, str]:
    work_description = clean_text(attrs.get("WorkDescription")) or clean_text(attrs.get("WorkType"))
    sub_description = clean_text(attrs.get("SubDescription")) or clean_text(attrs.get("SubType"))
    permit_type = (
        clean_text(attrs.get("PermitType"))
        or work_description
        or sub_description
    )
    description_parts = [part for part in (work_description, sub_description) if part]
    value = attrs.get("ValueOfConstruction")
    project_value = "" if value is None else str(value)
    applicant = clean_text(attrs.get("ApplicantOrganization"))

    return {
        "external_id": clean_text(attrs.get("PermitNumber")),
        "address": _project_address(attrs),
        "permit_type": permit_type,
        "project_value": project_value,
        "applicant": applicant,
        "issue_date": _parse_issue_date(attrs.get("IssuedDate")),
        "description": " / ".join(description_parts),
        "source": SURREY_SOURCE,
        "city": SURREY_CITY,
    }


def _issued_date_cutoff(days: int) -> str:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    return cutoff.strftime("%Y%m%d")


def _build_where_clause(*, days: int | None) -> str:
    if days is None or days <= 0:
        return "1=1"
    return f"IssuedDate >= '{_issued_date_cutoff(days)}'"


def _layer_page_size(session) -> int:
    response = polite_api_get(session, SURREY_PERMITS_API, params={"f": "pjson"})
    response.raise_for_status()
    payload = response.json()
    max_records = int(payload.get("maxRecordCount") or DEFAULT_PAGE_SIZE)
    return min(max_records, DEFAULT_PAGE_SIZE)


def _query_page(
    session,
    *,
    where: str,
    offset: int,
    page_size: int,
) -> tuple[list[dict[str, str]], int, bool]:
    response = polite_api_get(
        session,
        f"{SURREY_PERMITS_API}/query",
        params={
            "where": where,
            "outFields": OUT_FIELDS,
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": "IssuedDate,PermitNumber",
            "f": "json",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])

    exceeded = bool(payload.get("exceededTransferLimit"))
    features = payload.get("features") or []
    records = [
        _format_record(feature.get("attributes") or {})
        for feature in features
        if _project_address(feature.get("attributes") or {})
    ]
    return records, len(features), exceeded


def _count_matches(session, *, where: str) -> int:
    response = polite_api_get(
        session,
        f"{SURREY_PERMITS_API}/query",
        params={"where": where, "returnCountOnly": "true", "f": "json"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return int(payload.get("count") or 0)


def _should_fetch_next_page(*, raw_count: int, page_size: int, exceeded: bool) -> bool:
    if raw_count == 0:
        return False
    return exceeded or raw_count >= page_size


def iter_surrey_permits(*, days: int | None = None) -> Iterator[dict[str, str]]:
    session = create_session()
    page_size = _layer_page_size(session)
    where = _build_where_clause(days=days)
    total = _count_matches(session, where=where)
    mode = f"last {days} days" if days else "full history"
    print(f"[Surrey Permits] Fetching {mode}: {total} records (page_size={page_size})")

    offset = 0
    pages = 0
    fetched = 0
    while True:
        page, raw_count, exceeded = _query_page(
            session,
            where=where,
            offset=offset,
            page_size=page_size,
        )
        pages += 1
        if raw_count == 0:
            break

        fetched += raw_count
        yield from page
        offset += raw_count

        print(
            f"[Surrey Permits] Page {pages}: {raw_count} rows "
            f"(offset={offset}, yielded={len(page)}, exceeded={exceeded})"
        )

        if not _should_fetch_next_page(raw_count=raw_count, page_size=page_size, exceeded=exceeded):
            break

    print(f"[Surrey Permits] Completed pagination: {pages} pages, {fetched} API rows")


def scrape_surrey_permits(*, days: int | None = None, persist: bool = True) -> dict[str, Any]:
    """Scrape Surrey permits. Full history when days is None; incremental when days > 0."""
    records = list(iter_surrey_permits(days=days))
    return scrape_and_persist_permits(
        records,
        source=SURREY_SOURCE,
        city=SURREY_CITY,
        csv_path=SURREY_PERMITS_CSV,
        fieldnames=FIELDNAMES,
        days=days,
        persist=persist,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Surrey issued building permits")
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
    scrape_surrey_permits(days=args.days, persist=not args.no_persist)


if __name__ == "__main__":
    main()
