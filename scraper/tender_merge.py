from __future__ import annotations

import csv
from pathlib import Path

from scraper.config import MERX_OPEN_SOURCE
from scraper.models import Tender

PROVINCIAL_SOURCES = {
    MERX_OPEN_SOURCE,
    "bcbid.gov.bc.ca",
}


def merge_tenders_by_url(*groups: list[Tender]) -> list[Tender]:
    """Merge tender lists; earlier groups win on duplicate URLs."""
    merged: dict[str, Tender] = {}
    for group in groups:
        for tender in group:
            url = (tender.url or "").strip()
            if not url or url in merged:
                continue
            merged[url] = tender
    return list(merged.values())


def load_tenders_from_csv(csv_path: Path) -> list[Tender]:
    if not csv_path.exists():
        return []

    rows: list[Tender] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("url") or "").strip()
            if not url:
                continue
            rows.append(
                Tender(
                    title=row.get("title", ""),
                    organization=row.get("organization", ""),
                    category=row.get("category", ""),
                    posted_date=row.get("posted_date", ""),
                    closing_date=row.get("closing_date", ""),
                    estimated_value=row.get("estimated_value", ""),
                    location=row.get("location", ""),
                    tender_id=row.get("tender_id", ""),
                    url=url,
                    source=row.get("source", ""),
                )
            )
    return rows


def split_tenders_by_source(tenders: list[Tender]) -> tuple[list[Tender], list[Tender]]:
    federal: list[Tender] = []
    provincial: list[Tender] = []
    for tender in tenders:
        source = (tender.source or "").lower()
        if source in PROVINCIAL_SOURCES:
            provincial.append(tender)
        else:
            federal.append(tender)
    return federal, provincial
