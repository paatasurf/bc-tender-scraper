from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import (
    ARCHITECTURE_CATEGORY_LABEL,
    ARCH_TENDERS_CSV,
    MERX_ARCH_BC_LOCATION,
    MERX_ARCH_LIST_PATH,
    MERX_BASE_URL,
)
from scraper.models import ArchTender
from scraper.utils import clean_text, extract_label_value_map, polite_get, save_csv_rows

BC_LOCATION_PATTERN = re.compile(r"\bBC\b|British Columbia", re.I)


def _parse_listing_link(link, base_url: str) -> dict[str, str] | None:
    href = link.get("href")
    if not href:
        return None

    title_el = link.select_one(".rowTitle")
    buyer_el = link.select_one(".buyer-name")
    location_el = link.select_one(".location")
    closing_el = link.select_one(".closingDate .dateValue")
    remaining_el = link.select_one(".timeRemaining")
    tender_id_el = link.select_one(".accessibility-hidden")

    title = clean_text(title_el.get_text(" ", strip=True) if title_el else "")
    if not title:
        return None

    location = clean_text(location_el.get_text(" ", strip=True) if location_el else "")
    if location and not BC_LOCATION_PATTERN.search(location):
        return None

    status = "Open"
    if remaining_el:
        remaining = clean_text(remaining_el.get_text(" ", strip=True))
        if remaining:
            status = remaining

    return {
        "title": title,
        "company": clean_text(buyer_el.get_text(" ", strip=True) if buyer_el else ""),
        "deadline": clean_text(closing_el.get_text(" ", strip=True) if closing_el else ""),
        "status": status,
        "category": ARCHITECTURE_CATEGORY_LABEL,
        "url": urljoin(base_url, href),
        "tender_id": clean_text(tender_id_el.get_text(" ", strip=True) if tender_id_el else ""),
        "location": location,
    }


def _extract_detail_labels(soup: BeautifulSoup) -> dict[str, str]:
    labels = extract_label_value_map(soup)
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) != 2:
            continue
        key = clean_text(cells[0].get_text(" ", strip=True))
        val = clean_text(cells[1].get_text(" ", strip=True))
        if key and val and key not in labels:
            labels[key] = val
    return labels


def _extract_estimated_value(labels: dict[str, str]) -> str:
    for key, value in labels.items():
        key_lower = key.lower()
        if "value" in key_lower and ("estimated" in key_lower or "contract" in key_lower):
            return value
    return ""


def _parse_detail_page(soup: BeautifulSoup, listing: dict[str, str]) -> ArchTender:
    labels = _extract_detail_labels(soup)

    title = listing["title"]
    h1 = soup.find("h1")
    if h1:
        h1_text = clean_text(h1.get_text(" ", strip=True))
        if h1_text and len(h1_text) > len(title):
            title = h1_text.split(" - ", 1)[-1] if " - " in h1_text else h1_text

    company = (
        listing["company"]
        or labels.get("Issued by")
        or labels.get("Organization")
        or labels.get("Buyer Organization Name")
        or labels.get("Buyer")
    )
    deadline = (
        listing["deadline"]
        or labels.get("Closing Date")
        or labels.get("Closing Date and Time")
    )
    tender_id = (
        listing["tender_id"]
        or labels.get("Solicitation Number")
        or labels.get("Reference Number")
        or labels.get("Opportunity ID")
    )
    value = _extract_estimated_value(labels)
    status = listing["status"] or labels.get("Status", "Open")

    return ArchTender(
        title=title,
        company=company,
        value=value,
        deadline=deadline,
        status=status,
        category=listing["category"],
        url=listing["url"],
        tender_id=tender_id,
    )


def _iter_listing_pages(
    session: requests.Session,
    params: dict[str, str],
) -> Iterator[dict[str, str]]:
    page = 1
    seen_urls: set[str] = set()

    while True:
        page_params = dict(params)
        if page > 1:
            page_params["pageNumber"] = str(page)

        list_url = urljoin(MERX_BASE_URL, MERX_ARCH_LIST_PATH)
        print(f"[MERX Architecture] Fetching listing page {page}...")
        response = polite_get(session, list_url, params=page_params)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.select("a.solicitation-link")
        if not links:
            break

        new_rows = 0
        for link in links:
            listing = _parse_listing_link(link, MERX_BASE_URL)
            if not listing or listing["url"] in seen_urls:
                continue
            seen_urls.add(listing["url"])
            new_rows += 1
            yield listing

        next_link = soup.find("link", rel="next")
        if new_rows == 0 or not next_link:
            break
        page += 1


def scrape_merx_architecture_tenders(session: requests.Session) -> list[ArchTender]:
    params = {"location": MERX_ARCH_BC_LOCATION}
    listings = list(_iter_listing_pages(session, params))
    print(f"[MERX Architecture] Found {len(listings)} British Columbia opportunities")

    tenders: list[ArchTender] = []
    for index, listing in enumerate(listings, start=1):
        print(f"[MERX Architecture] Parsing detail {index}/{len(listings)}: {listing['title'][:70]}")
        response = polite_get(session, listing["url"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        tenders.append(_parse_detail_page(soup, listing))

    save_arch_tenders(tenders)
    print(f"[MERX Architecture] Saved {len(tenders)} tenders to {ARCH_TENDERS_CSV}")
    return tenders


def save_arch_tenders(tenders: list[ArchTender], csv_path: str = ARCH_TENDERS_CSV) -> None:
    fieldnames = ["title", "company", "value", "deadline", "status", "category", "url", "tender_id"]
    save_csv_rows((tender.to_dict() for tender in tenders), csv_path, fieldnames)
