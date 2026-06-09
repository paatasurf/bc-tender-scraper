from __future__ import annotations

import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from scraper.bcbid_common import (
    extract_estimated_value,
    iter_browse_pages,
    load_bcbid_cookies,
    parse_grid_row,
)
from scraper.config import ARCHITECTURE_CATEGORY_LABEL, ARCH_TENDERS_CSV
from scraper.models import ArchTender
from scraper.utils import (
    clean_text,
    extract_label_value_map,
    is_browser_check,
    polite_get,
    save_csv_rows,
)


def is_architecture_engineering_listing_hint(*parts: str) -> bool:
    blob = " ".join(clean_text(part) for part in parts if part).lower()
    if ARCHITECTURE_CATEGORY_LABEL.lower() in blob:
        return True
    if "architect" in blob:
        return True
    return "engineering" in blob or "engineer" in blob


def is_architecture_engineering_category(*parts: str) -> bool:
    blob = " ".join(clean_text(part) for part in parts if part).lower()
    if ARCHITECTURE_CATEGORY_LABEL.lower() in blob:
        return True
    return "architect" in blob and ("engineering" in blob or "engineer" in blob)


def parse_architecture_detail(session: requests.Session, summary: dict[str, str]) -> ArchTender | None:
    print(f"[BC Bid Architecture] Parsing detail: {summary['title'][:70]}")
    response = polite_get(session, summary["url"])
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    if is_browser_check(soup):
        return None

    labels = extract_label_value_map(soup)
    title = labels.get("Opportunity Description", summary["title"])
    company = summary["organization"] or labels.get("Issued by", "")
    category = labels.get("Industry Category", summary["commodities"] or summary["type"])
    deadline = labels.get("Closing Date and Time", summary["closing_date"])
    status = summary["status"] or labels.get("Status", "Open")
    value = extract_estimated_value(labels)

    if not is_architecture_engineering_category(category, summary["commodities"], summary["type"], title):
        return None

    tender_id = labels.get("Opportunity ID", summary["tender_id"])
    if not tender_id:
        match = re.search(r"/(\d+)\s*$", summary["url"])
        tender_id = match.group(1) if match else summary["tender_id"]

    return ArchTender(
        title=title,
        company=company,
        value=value,
        deadline=deadline,
        status=status,
        category=category or ARCHITECTURE_CATEGORY_LABEL,
        url=summary["url"],
        tender_id=tender_id,
    )


def save_arch_tenders(tenders: list[ArchTender], csv_path: str = ARCH_TENDERS_CSV) -> None:
    fieldnames = ["title", "company", "value", "deadline", "status", "category", "url", "tender_id"]
    save_csv_rows((tender.to_dict() for tender in tenders), csv_path, fieldnames)


def scrape_bcbid_architecture_tenders(
    session: requests.Session,
    cookie_path: Path | None = None,
) -> list[ArchTender]:
    load_bcbid_cookies(session, cookie_path)

    summaries: list[dict[str, str]] = []
    for soup in iter_browse_pages(session, log_prefix="[BC Bid Architecture]"):
        grid = soup.find("table", id="body_x_grid_grd")
        if not grid:
            continue
        for row in grid.find_all("tr", attrs={"data-id": True}):
            summary = parse_grid_row(row)
            if summary and is_architecture_engineering_listing_hint(
                summary["commodities"], summary["title"], summary["type"]
            ):
                summaries.append(summary)

    print(f"[BC Bid Architecture] Found {len(summaries)} architecture & engineering opportunities")

    tenders: list[ArchTender] = []
    for index, summary in enumerate(summaries, start=1):
        print(f"[BC Bid Architecture] Checking match {index}/{len(summaries)}")
        tender = parse_architecture_detail(session, summary)
        if tender:
            tenders.append(tender)

    save_arch_tenders(tenders)
    print(f"[BC Bid Architecture] Saved {len(tenders)} tenders to {ARCH_TENDERS_CSV}")
    return tenders
