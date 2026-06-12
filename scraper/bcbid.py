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
from scraper.models import Tender
from scraper.bcbid_auth import handle_bcbid_auth_failure, is_bcbid_auth_failure
from scraper.utils import (
    clean_text,
    extract_label_value_map,
    matches_target_category,
    polite_get,
)


def _parse_detail_page(session: requests.Session, summary: dict[str, str]) -> Tender | None:
    print(f"[BC Bid] Parsing detail: {summary['title'][:70]}")
    response = polite_get(session, summary["url"])
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    if is_bcbid_auth_failure(soup, html=response.text):
        handle_bcbid_auth_failure(soup, html=response.text)
        return None

    labels = extract_label_value_map(soup)
    title = labels.get("Opportunity Description", summary["title"])
    organization = summary["organization"] or labels.get("Issued by", "")
    category = labels.get("Industry Category", summary["commodities"] or summary["type"])

    location_parts = [
        labels.get("Region"),
        labels.get("City"),
        labels.get("Province"),
    ]
    location = ", ".join(part for part in map(clean_text, location_parts) if part)
    if not location:
        location = "British Columbia"

    estimated_value = extract_estimated_value(labels)

    tender_id = labels.get("Opportunity ID", summary["tender_id"])
    if not tender_id:
        match = re.search(r"/(\d+)\s*$", summary["url"])
        tender_id = match.group(1) if match else summary["tender_id"]

    tender = Tender(
        title=title,
        organization=organization,
        category=category,
        posted_date=labels.get("Issue Date", summary["posted_date"]),
        closing_date=labels.get("Closing Date and Time", summary["closing_date"]),
        estimated_value=estimated_value,
        location=location,
        tender_id=tender_id,
        url=summary["url"],
        source="bcbid.gov.bc.ca",
    )

    if not matches_target_category(tender.category, tender.title, summary["commodities"], summary["type"]):
        return None
    return tender


def scrape_bcbid_tenders(
    session: requests.Session,
    cookie_path: Path | None = None,
) -> list[Tender]:
    load_bcbid_cookies(session, cookie_path)

    summaries: list[dict[str, str]] = []
    for soup in iter_browse_pages(session):
        grid = soup.find("table", id="body_x_grid_grd")
        if not grid:
            continue
        for row in grid.find_all("tr", attrs={"data-id": True}):
            summary = parse_grid_row(row)
            if summary:
                summaries.append(summary)

    print(f"[BC Bid] Found {len(summaries)} open opportunities in listing")

    tenders: list[Tender] = []
    for index, summary in enumerate(summaries, start=1):
        if not matches_target_category(
            summary["commodities"], summary["title"], summary["type"]
        ):
            continue
        print(f"[BC Bid] Checking match {index}/{len(summaries)}")
        tender = _parse_detail_page(session, summary)
        if tender:
            tenders.append(tender)

    return tenders
