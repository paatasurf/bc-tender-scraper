from __future__ import annotations

from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import (
    CONTRACT_AWARDS_CSV,
    FEDERAL_CATEGORY_CONSTRUCTION,
    FEDERAL_LIST_PATH,
    FEDERAL_LOCATION_BC,
    FEDERAL_STATUS_AWARDED,
)
from scraper.federal import _resolve_federal_base
from scraper.utils import clean_text, polite_get, save_csv_rows

FIELDNAMES = ["winner_company", "contract_value", "date", "tender_title", "url"]


def _iter_award_listings(
    session: requests.Session,
    base_url: str,
) -> Iterator[dict[str, str]]:
    page = 0
    seen_urls: set[str] = set()

    while True:
        params = {
            "status[0]": FEDERAL_STATUS_AWARDED,
            "category[0]": FEDERAL_CATEGORY_CONSTRUCTION,
            "location[0]": FEDERAL_LOCATION_BC,
            "items_per_page": "100",
        }
        if page:
            params["page"] = str(page)

        list_url = urljoin(base_url, FEDERAL_LIST_PATH)
        print(f"[Contract Awards] Fetching awarded listings page {page + 1}...")
        response = polite_get(session, list_url, params=params)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if not table:
            break

        rows = table.find_all("tr")[1:]
        if not rows:
            break

        new_rows = 0
        for row in rows:
            link = row.find("a", href=lambda href: href and "award-notice" in href)
            if not link:
                continue

            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            url = urljoin(base_url, link["href"])
            if url in seen_urls:
                continue
            seen_urls.add(url)
            new_rows += 1

            yield {
                "tender_title": clean_text(link.get_text(" ", strip=True)),
                "category": cells[1] if len(cells) > 1 else "",
                "date": cells[2] if len(cells) > 2 else "",
                "url": url,
            }

        if new_rows == 0 or len(rows) < int(params["items_per_page"]):
            break
        page += 1


def _parse_award_detail(soup: BeautifulSoup, listing: dict[str, str]) -> dict[str, str]:
    title = clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else listing["tender_title"]

    winner = ""
    winner_el = soup.select_one(".contract-company")
    if winner_el:
        winner = clean_text(winner_el.get_text(" ", strip=True))

    contract_value = ""
    value_el = soup.select_one(".field--name-field-award-contract-total-value .sr-only")
    if value_el:
        contract_value = clean_text(value_el.get_text(" ", strip=True))
    if not contract_value:
        for field in soup.select(".field--name-field-award-contract-total-value .field--item"):
            text = clean_text(field.get_text(" ", strip=True))
            if text:
                contract_value = text
                break

    award_date = listing["date"]
    date_el = soup.select_one(".field--name-field-award-contract-award-date .field--item")
    if date_el:
        award_date = clean_text(date_el.get_text(" ", strip=True)) or award_date

    return {
        "winner_company": winner,
        "contract_value": contract_value,
        "date": award_date,
        "tender_title": title,
        "url": listing["url"],
    }


def scrape_contract_awards(session: requests.Session) -> list[dict[str, str]]:
    base_url = _resolve_federal_base(session)
    print(f"[Contract Awards] Using host {base_url}")

    listings = list(_iter_award_listings(session, base_url))
    print(f"[Contract Awards] Found {len(listings)} awarded construction notices in BC")

    awards: list[dict[str, str]] = []
    for index, listing in enumerate(listings, start=1):
        print(f"[Contract Awards] Parsing award {index}/{len(listings)}: {listing['tender_title'][:70]}")
        response = polite_get(session, listing["url"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        awards.append(_parse_award_detail(soup, listing))

    save_csv_rows(awards, CONTRACT_AWARDS_CSV, FIELDNAMES)
    print(f"[Contract Awards] Saved {len(awards)} awards to {CONTRACT_AWARDS_CSV}")
    return awards
