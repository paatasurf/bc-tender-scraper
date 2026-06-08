from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import (
    FEDERAL_CATEGORY_CONSTRUCTION,
    FEDERAL_CATEGORY_SERVICES,
    FEDERAL_HOSTS,
    FEDERAL_LIST_PATH,
    FEDERAL_LOCATION_BC,
    FEDERAL_STATUS_OPEN,
)
from scraper.models import Tender
from scraper.utils import clean_text, matches_target_category, polite_get


def _resolve_federal_base(session: requests.Session) -> str:
    for host in FEDERAL_HOSTS:
        try:
            response = polite_get(session, host, allow_redirects=True)
            if response.ok:
                return response.url.rstrip("/").split("/en")[0]
        except requests.RequestException:
            continue
    return FEDERAL_HOSTS[-1]


def _parse_listing_row(row, base_url: str) -> dict[str, str] | None:
    link = row.find("a", href=lambda href: href and "tender-notice" in href)
    if not link:
        return None

    cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
    if len(cells) < 5:
        return None

    href = link["href"]
    return {
        "title": clean_text(link.get_text(" ", strip=True)),
        "category": cells[1],
        "posted_date": re.sub(r"(?i)amended$", "", cells[2]).strip(),
        "closing_date": cells[3],
        "organization": cells[4],
        "url": urljoin(base_url, href),
    }


def _iter_listing_pages(
    session: requests.Session,
    base_url: str,
    params: dict[str, str],
) -> Iterator[dict[str, str]]:
    page = 0
    seen_urls: set[str] = set()

    while True:
        page_params = {**params, "items_per_page": "100"}
        if page:
            page_params["page"] = str(page)

        list_url = urljoin(base_url, FEDERAL_LIST_PATH)
        print(f"[Federal] Fetching listing page {page + 1}...")
        response = polite_get(session, list_url, params=page_params)
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
            listing = _parse_listing_row(row, base_url)
            if not listing or listing["url"] in seen_urls:
                continue
            seen_urls.add(listing["url"])
            new_rows += 1
            yield listing

        if new_rows == 0 or len(rows) < int(page_params["items_per_page"]):
            break
        page += 1


def _parse_detail_page(soup: BeautifulSoup, listing: dict[str, str]) -> Tender:
    labels = {}
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            key = clean_text(dt.get_text(" ", strip=True))
            val = clean_text(dd.get_text(" ", strip=True))
            if key:
                labels[key] = val

    title = clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else listing["title"]
    entity = soup.select_one(".field--name-field-tender-contracting-entity .field--item")
    organization = (
        labels.get("Organization")
        or listing["organization"]
        or clean_text(entity.get_text(" ", strip=True) if entity else "")
    )

    posted_date = listing["posted_date"]
    publication = soup.select_one(".field--name-field-tender-publication-date .field--item")
    if publication:
        posted_date = clean_text(publication.get_text(" ", strip=True)) or posted_date

    closing_date = listing["closing_date"]
    closing = soup.select_one(".field--name-field-tender-closing-date .field--item")
    if closing:
        closing_date = clean_text(closing.get_text(" ", strip=True)) or closing_date

    location = labels.get("Region(s) of delivery", "")
    if not location:
        region = soup.select_one(".field--name-field-tender-delivery-regions .field--item")
        if region:
            location = clean_text(region.get_text(" ", strip=True))

    tender_id = ""
    solicitation = soup.select_one(".field--name-field-tender-solicitation-number .field--item")
    if solicitation:
        tender_id = clean_text(solicitation.get_text(" ", strip=True))
    if not tender_id:
        tender_id = listing["url"].rstrip("/").split("/")[-1]

    estimated_value = ""
    for field in soup.select(".field"):
        label = field.find(class_=lambda c: c and "label" in c)
        if not label:
            continue
        label_text = clean_text(label.get_text(" ", strip=True)).lower()
        if "estimated" in label_text and "value" in label_text:
            value = field.find(class_=lambda c: c and "item" in c)
            if value:
                estimated_value = clean_text(value.get_text(" ", strip=True))
                break

    category = listing["category"]
    if not matches_target_category(category, title):
        category_blob = clean_text(soup.get_text(" ", strip=True))
        if matches_target_category(category_blob):
            for keyword in ("construction", "architecture", "engineering"):
                if keyword in category_blob.lower() and keyword.title() not in category:
                    category = keyword.title()
                    break

    return Tender(
        title=title,
        organization=organization,
        category=category,
        posted_date=posted_date,
        closing_date=closing_date,
        estimated_value=estimated_value,
        location=location or "British Columbia",
        tender_id=tender_id,
        url=listing["url"],
        source="buyandsell.gc.ca",
    )


def scrape_federal_tenders(session: requests.Session) -> list[Tender]:
    base_url = _resolve_federal_base(session)
    print(f"[Federal] Using host {base_url}")

    listings: list[dict[str, str]] = []
    query_sets = [
        {
            "status[0]": FEDERAL_STATUS_OPEN,
            "category[0]": FEDERAL_CATEGORY_CONSTRUCTION,
            "location[0]": FEDERAL_LOCATION_BC,
        },
        {
            "status[0]": FEDERAL_STATUS_OPEN,
            "category[0]": FEDERAL_CATEGORY_SERVICES,
            "location[0]": FEDERAL_LOCATION_BC,
        },
    ]

    for params in query_sets:
        for listing in _iter_listing_pages(session, base_url, params):
            if params["category[0]"] == FEDERAL_CATEGORY_SERVICES and not matches_target_category(
                listing["title"], listing["category"]
            ):
                continue
            listings.append(listing)

    print(f"[Federal] Found {len(listings)} candidate tender notices")

    tenders: list[Tender] = []
    for index, listing in enumerate(listings, start=1):
        print(f"[Federal] Parsing detail {index}/{len(listings)}: {listing['title'][:70]}")
        response = polite_get(session, listing["url"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        tender = _parse_detail_page(soup, listing)
        if matches_target_category(tender.category, tender.title):
            tenders.append(tender)

    return tenders
