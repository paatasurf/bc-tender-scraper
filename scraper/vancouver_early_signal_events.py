"""Vancouver rezoning and development permit applications from Open Data.

COV publishes active development proposals through the city-projects-package-site
dataset (weekly refresh). Records are classified into rezoning_application and
development_permit_application signal types for the early_signal_events table.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Literal

from scraper.config import (
    VANCOUVER_CITY_PROJECTS_API,
    VANCOUVER_MUNICIPALITY,
    VANCOUVER_OPEN_DATA_SOURCE,
)
from scraper.utils import clean_text, create_session, polite_api_get

PAGE_SIZE = 100
PROJECT_ID_RE = re.compile(r"selProjectID=(\d+)", re.I)

SignalType = Literal["rezoning_application", "development_permit_application"]

REZONING_PATTERNS = (
    re.compile(r"\brezoning\b", re.I),
    re.compile(r"\bcd-?1\b", re.I),
    re.compile(r"\btext amendment\b", re.I),
    re.compile(r"\bodp\b", re.I),
    re.compile(r"\bofficial development plan\b", re.I),
    re.compile(r"\bzoning by-?law\b", re.I),
    re.compile(r"\bby-?law amendment\b", re.I),
)

DEVELOPMENT_PERMIT_PATTERNS = (
    re.compile(r"\bdevelopment permit\b", re.I),
    re.compile(r"\bmixed[- ]use\b", re.I),
    re.compile(r"\bmultiple dwelling\b", re.I),
    re.compile(r"\bcommercial development\b", re.I),
    re.compile(r"\bresidential development\b", re.I),
    re.compile(r"\bchange of use\b", re.I),
    re.compile(r"\bnew construction\b", re.I),
    re.compile(r"\btownhome?s?\b", re.I),
    re.compile(r"\b\d+[- ]storey\b", re.I),
    re.compile(r"\bhigh[- ]rise\b", re.I),
    re.compile(r"\btower\b", re.I),
    re.compile(r"\bone[- ]family dwelling\b", re.I),
    re.compile(r"\btwo[- ]family dwelling\b", re.I),
    re.compile(r"\bsingle family\b", re.I),
    re.compile(r"\bmultiple conversion\b", re.I),
    re.compile(r"\bnew building\b", re.I),
    re.compile(r"\baddition to\b", re.I),
)

SKIP_PATTERNS = (
    re.compile(r"^\s*construction\s*$", re.I),
    re.compile(r"\broad ahead\b", re.I),
    re.compile(r"\binterior (and )?exterior alterations?\b", re.I),
    re.compile(r"^\s*interior alterations?\s*$", re.I),
    re.compile(r"^\s*exterior alterations?\s*$", re.I),
    re.compile(r"^\s*parking lot\s*$", re.I),
)


def extract_project_id(url: str | None) -> str:
    match = PROJECT_ID_RE.search(url or "")
    return match.group(1) if match else ""


def classify_city_project(project_title: str | None) -> SignalType | None:
    title = clean_text(project_title)
    if not title:
        return None
    if any(pattern.search(title) for pattern in SKIP_PATTERNS):
        return None
    if any(pattern.search(title) for pattern in REZONING_PATTERNS):
        return "rezoning_application"
    if any(pattern.search(title) for pattern in DEVELOPMENT_PERMIT_PATTERNS):
        return "development_permit_application"
    return None


def _format_event(record: dict[str, Any], *, signal_type: SignalType) -> dict[str, str] | None:
    url_link = clean_text(record.get("url_link"))
    project_id = extract_project_id(url_link)
    if not project_id:
        return None

    return {
        "external_id": f"van-proj-{project_id}",
        "source": VANCOUVER_OPEN_DATA_SOURCE,
        "transaction_date": "",
        "municipality": VANCOUVER_MUNICIPALITY,
        "region": clean_text(record.get("geo_local_area")),
        "property_type": clean_text(record.get("project_title")),
        "signal_type": signal_type,
        "url_link": url_link,
    }


def iter_city_project_events() -> Iterator[dict[str, str]]:
    session = create_session()
    offset = 0
    total_count = None

    while True:
        response = polite_api_get(
            session,
            VANCOUVER_CITY_PROJECTS_API,
            params={"limit": PAGE_SIZE, "offset": offset},
        )
        response.raise_for_status()
        payload = response.json()

        if total_count is None:
            total_count = payload.get("total_count", 0)
            print(f"[Vancouver Early Signals] city-projects-package-site: {total_count} records")

        results = payload.get("results", [])
        if not results:
            break

        for record in results:
            signal_type = classify_city_project(record.get("project_title"))
            if signal_type is None:
                continue
            event = _format_event(record, signal_type=signal_type)
            if event is not None:
                yield event

        offset += len(results)
        if offset >= total_count:
            break


def scrape_vancouver_rezoning_applications(*, persist: bool = True) -> dict[str, Any]:
    return _scrape_filtered(signal_type="rezoning_application", persist=persist)


def scrape_vancouver_development_permit_applications(*, persist: bool = True) -> dict[str, Any]:
    return _scrape_filtered(signal_type="development_permit_application", persist=persist)


def scrape_vancouver_early_signal_events(*, persist: bool = True) -> dict[str, Any]:
    """Scrape and persist both rezoning and development permit application signals."""
    records = list(iter_city_project_events())
    return _persist_records(records, persist=persist)


def _scrape_filtered(*, signal_type: SignalType, persist: bool) -> dict[str, Any]:
    records = [row for row in iter_city_project_events() if row["signal_type"] == signal_type]
    result = _persist_records(records, persist=persist)
    result["signal_type"] = signal_type
    return result


def _persist_records(records: list[dict[str, str]], *, persist: bool) -> dict[str, Any]:
    rezoning_count = sum(1 for row in records if row["signal_type"] == "rezoning_application")
    development_count = sum(
        1 for row in records if row["signal_type"] == "development_permit_application"
    )

    result: dict[str, Any] = {
        "source": VANCOUVER_OPEN_DATA_SOURCE,
        "dataset": "city-projects-package-site",
        "municipality": VANCOUVER_MUNICIPALITY,
        "events_scraped": len(records),
        "rezoning_applications": rezoning_count,
        "development_permit_applications": development_count,
    }

    if persist and records:
        from db.connection import get_session, init_db
        from db.early_signal_import import upsert_early_signal_events

        init_db()
        session = get_session()
        try:
            result["events_persisted"] = upsert_early_signal_events(session, records)
        finally:
            session.close()
    else:
        result["events_persisted"] = 0

    print(
        f"[Vancouver Early Signals] Scraped {len(records)} events "
        f"({rezoning_count} rezoning, {development_count} development permits); "
        f"persisted {result.get('events_persisted', 0)}"
    )
    return result
