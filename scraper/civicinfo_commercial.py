from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.commercial_common import make_commercial_tender
from scraper.config import CIVICINFO_BASE_URL, CIVICINFO_BIDS_URL
from scraper.models import CommercialTender
from scraper.utils import clean_text, fetch_html

EXPIRES_PATTERN = re.compile(r"Expires:\s*(.+)$", re.I)
BID_ID_PATTERN = re.compile(r"bidid=(\d+)")


def _parse_listing_item(item) -> CommercialTender | None:
    title_el = item.select_one(".title a")
    if not title_el:
        return None

    title = clean_text(title_el.get_text(" ", strip=True))
    href = title_el.get("href", "")
    if not href:
        return None

    url = href if href.startswith("http") else urljoin(CIVICINFO_BASE_URL, href)
    bid_match = BID_ID_PATTERN.search(href)
    tender_id = bid_match.group(1) if bid_match else ""

    opportunity_type = ""
    location = ""
    deadline = ""

    for paragraph in item.find_all("p", class_="mb-1"):
        text = clean_text(paragraph.get_text(" ", strip=True))
        if paragraph.find("i", class_=lambda value: value and "map-marker" in value):
            location = text.split(", BC")[0].strip()
        elif EXPIRES_PATTERN.search(text):
            deadline_match = EXPIRES_PATTERN.search(text)
            deadline = clean_text(deadline_match.group(1)) if deadline_match else ""
        elif text and not opportunity_type:
            opportunity_type = text

    company = location or "CivicInfo BC"
    if opportunity_type:
        company = f"{company} · {opportunity_type}"

    return make_commercial_tender(
        title=title,
        company=company,
        url=url,
        source="civicinfo",
        deadline=deadline,
        status="Open",
        tender_id=tender_id,
    )


def scrape_civicinfo_commercial(session: requests.Session) -> list[CommercialTender]:
    print("[CivicInfo] Fetching municipal bids...")
    html = fetch_html(session, CIVICINFO_BIDS_URL, params={"per_page": "100"})
    soup = BeautifulSoup(html, "html.parser")

    listings = soup.select(".directory-listings li")
    tenders: list[CommercialTender] = []
    seen_urls: set[str] = set()

    for item in listings:
        tender = _parse_listing_item(item)
        if tender and tender.url not in seen_urls:
            seen_urls.add(tender.url)
            tenders.append(tender)

    print(f"[CivicInfo] Found {len(tenders)} commercial opportunities")
    return tenders
