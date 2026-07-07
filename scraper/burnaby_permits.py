"""Burnaby issued building permits — daily PDF reports (Feature 011)."""

from __future__ import annotations

import argparse
import io
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

from pypdf import PdfReader

from scraper.config import (
    BURNABY_CITY,
    BURNABY_PERMITS_CSV,
    BURNABY_PERMITS_INDEX_URL,
    BURNABY_SOURCE,
)
from scraper.permit_persist import scrape_and_persist_permits
from scraper.utils import clean_text, create_session, polite_get

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

PERMIT_NUM_RE = re.compile(r"BLD\d{2}-\d{5}")
VALUE_RE = re.compile(r"\$([\d,]+\.\d{2})")
ISSUED_ON_RE = re.compile(r"Permits Issued On:\s*(.+)", re.I)
PDF_LINK_RE = re.compile(
    r'href="(/sites/default/files/acquiadam/[^"]+\.pdf)"[^>]*>([^<]+)',
    re.I,
)
PDF_DATE_RE = re.compile(
    r"(?P<month>[A-Za-z]+)-(?P<day>\d{1,2})-(?P<year>\d{4})\.pdf",
    re.I,
)
STREET_SUFFIX = (
    r"ST|AVE|DR|WAY|CRT|BLVD|HWY|ROAD|RD|STREET|CRES|GATE|GROVE|LANE|PL|MALL|DIVER|LOOP"
)
SITE_ADDRESS_BEFORE_LOT_RE = re.compile(
    rf"\n([\d][^\n]*(?:{STREET_SUFFIX})\b[^\n]*)\s*\n\s*LOT:",
    re.I,
)
SITE_ADDRESS_BEFORE_LEGAL_RE = re.compile(
    rf"\n([\d][^\n]*(?:{STREET_SUFFIX})\b[^\n]*)\s*\nLegal Description",
    re.I,
)
PERMIT_CATEGORY_RE = re.compile(
    r"((?:Building|Demolition|Plumbing|Electrical|Sign|Sprinkler)[^\n$]{0,80})",
    re.I,
)
APPLICANT_RE = re.compile(
    r"Applicant Name\s*\n?\s*(.+?)(?:\nContractor Name|\nContractor Address|\nDescription|\Z)",
    re.S,
)
DESCRIPTION_RE = re.compile(
    r"Description\s*\n?\s*(.+?)(?:\nSite Address|\nLegal Description|\nPermit\s*\n|\nValue of Work|\Z)",
    re.S,
)


def _parse_issue_date_header(text: str) -> str:
    match = ISSUED_ON_RE.search(text)
    if not match:
        return ""
    raw = clean_text(match.group(1))
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _parse_pdf_label_date(label: str) -> date | None:
    match = PDF_DATE_RE.search(label.strip())
    if not match:
        return None
    month_raw = match.group("month").lower()
    if month_raw.startswith("aprl"):
        month_raw = "april"
    for fmt in ("%B-%d-%Y", "%b-%d-%Y"):
        try:
            return datetime.strptime(
                f"{month_raw}-{match.group('day')}-{match.group('year')}",
                fmt,
            ).date()
        except ValueError:
            continue
    return None


def _extract_address(block: str) -> str:
    for pattern in (SITE_ADDRESS_BEFORE_LOT_RE, SITE_ADDRESS_BEFORE_LEGAL_RE):
        match = pattern.search(block)
        if match:
            address = re.sub(r"\s+", " ", match.group(1)).strip()
            if address and ", BC" not in address.upper():
                return address
    return ""


def _parse_permit_block(block: str, permit_num: str, issue_date: str) -> dict[str, str]:
    value_match = VALUE_RE.search(block)
    project_value = value_match.group(1).replace(",", "") if value_match else ""

    cat_match = PERMIT_CATEGORY_RE.search(block)
    permit_type = clean_text(cat_match.group(1)) if cat_match else ""

    applicant = ""
    app_match = APPLICANT_RE.search(block)
    if app_match:
        applicant = clean_text(app_match.group(1))

    description = ""
    desc_match = DESCRIPTION_RE.search(block)
    if desc_match:
        description = clean_text(desc_match.group(1))

    return {
        "external_id": permit_num,
        "address": _extract_address(block),
        "permit_type": permit_type,
        "project_value": project_value,
        "applicant": applicant,
        "issue_date": issue_date,
        "description": description,
        "source": BURNABY_SOURCE,
        "city": BURNABY_CITY,
    }


def _parse_pdf_bytes(content: bytes) -> list[dict[str, str]]:
    reader = PdfReader(io.BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    issue_date = _parse_issue_date_header(text)
    matches = list(PERMIT_NUM_RE.finditer(text))
    records: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        record = _parse_permit_block(text[start:end], match.group(), issue_date)
        if record["address"] or record["permit_type"]:
            records.append(record)
    return records


def _list_pdf_sources(session) -> list[tuple[str, str, date | None]]:
    html = polite_get(session, BURNABY_PERMITS_INDEX_URL).text
    sources: list[tuple[str, str, date | None]] = []
    seen: set[str] = set()
    for href, label in PDF_LINK_RE.findall(html):
        if href in seen:
            continue
        seen.add(href)
        sources.append((label.strip(), href, _parse_pdf_label_date(label)))
    return sources


def _date_cutoff(days: int) -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=days)


def _filter_pdf_sources(
    sources: list[tuple[str, str, date | None]],
    *,
    days: int | None,
) -> list[tuple[str, str, date | None]]:
    if days is None or days <= 0:
        return sources
    cutoff = _date_cutoff(days)
    today = datetime.now(timezone.utc).date()
    return [
        source
        for source in sources
        if source[2] is not None and cutoff <= source[2] <= today
    ]


def iter_burnaby_permits(*, days: int | None = None) -> Iterator[dict[str, str]]:
    session = create_session()
    all_sources = _list_pdf_sources(session)
    sources = _filter_pdf_sources(all_sources, days=days)
    mode = f"last {days} days" if days else "full history"
    print(f"[Burnaby Permits] Fetching {mode}: {len(sources)} PDF reports")

    for page_index, (label, href, _pdf_date) in enumerate(sources, start=1):
        url = f"https://www.burnaby.ca{href}"
        response = polite_get(session, url)
        response.raise_for_status()
        records = _parse_pdf_bytes(response.content)
        print(
            f"[Burnaby Permits] PDF {page_index}/{len(sources)} {label}: "
            f"{len(records)} permits"
        )
        yield from records


def scrape_burnaby_permits(*, days: int | None = None, persist: bool = True) -> dict[str, Any]:
    """Scrape Burnaby permits. Full history when days is None; incremental when days > 0."""
    records = list(iter_burnaby_permits(days=days))
    return scrape_and_persist_permits(
        records,
        source=BURNABY_SOURCE,
        city=BURNABY_CITY,
        csv_path=BURNABY_PERMITS_CSV,
        fieldnames=FIELDNAMES,
        days=days,
        persist=persist,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Burnaby issued building permits")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Incremental window in days (default: 7)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full historical load (all PDF reports on the index page)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Write CSV only; do not upsert into PostgreSQL",
    )
    args = parser.parse_args()
    days = None if args.full else args.days
    scrape_burnaby_permits(days=days, persist=not args.no_persist)


if __name__ == "__main__":
    main()
