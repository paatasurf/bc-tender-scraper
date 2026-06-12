from __future__ import annotations

import re
from collections.abc import Callable
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import MERX_BASE_URL
from scraper.utils import clean_text, polite_get

BC_LOCATION_PATTERN = re.compile(r"\bBC\b|British Columbia", re.I)
CLOSING_DATE_PATTERN = re.compile(r"(\d{4}/\d{2}/\d{2})")


def parse_merx_listing_link(link, base_url: str, *, require_bc: bool = True) -> dict[str, str] | None:
    href = link.get("href")
    if not href:
        return None

    title_el = link.select_one(".rowTitle")
    buyer_el = link.select_one(".buyer-name")
    location_el = link.select_one(".location")
    closing_el = link.select_one(".closingDate .dateValue") or link.select_one(".closingDate")
    remaining_el = link.select_one(".timeRemaining")
    tender_id_el = link.select_one(".accessibility-hidden")

    title = clean_text(title_el.get_text(" ", strip=True) if title_el else "")
    if not title:
        return None

    location = clean_text(location_el.get_text(" ", strip=True) if location_el else "")
    if require_bc and location and not BC_LOCATION_PATTERN.search(location):
        return None

    closing_raw = clean_text(closing_el.get_text(" ", strip=True) if closing_el else "")
    closing_match = CLOSING_DATE_PATTERN.search(closing_raw)
    closing_date = closing_match.group(1) if closing_match else closing_raw

    status = "Open"
    if remaining_el:
        remaining = clean_text(remaining_el.get_text(" ", strip=True))
        if remaining:
            status = remaining

    return {
        "title": title,
        "organization": clean_text(buyer_el.get_text(" ", strip=True) if buyer_el else ""),
        "closing_date": closing_date,
        "status": status,
        "location": location or "British Columbia",
        "url": urljoin(base_url, href),
        "tender_id": clean_text(tender_id_el.get_text(" ", strip=True) if tender_id_el else ""),
    }


def iter_merx_listing_pages(
    session: requests.Session,
    list_path: str,
    params: dict[str, str],
    *,
    log_prefix: str = "[MERX]",
    require_bc: bool = True,
    row_filter: Callable[[dict[str, str]], bool] | None = None,
) -> Iterator[dict[str, str]]:
    page = 1
    seen_urls: set[str] = set()

    while True:
        page_params = dict(params)
        if page > 1:
            page_params["pageNumber"] = str(page)

        list_url = urljoin(MERX_BASE_URL, list_path)
        print(f"{log_prefix} Fetching listing page {page}...")
        response = polite_get(session, list_url, params=page_params)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.select("a.solicitation-link")
        if not links:
            break

        new_rows = 0
        for link in links:
            listing = parse_merx_listing_link(link, MERX_BASE_URL, require_bc=require_bc)
            if not listing or listing["url"] in seen_urls:
                continue
            if row_filter is not None and not row_filter(listing):
                continue
            seen_urls.add(listing["url"])
            new_rows += 1
            yield listing

        next_link = soup.find("link", rel="next")
        if new_rows == 0 or not next_link:
            break
        page += 1
