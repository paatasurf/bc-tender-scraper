from __future__ import annotations

import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.commercial_common import make_commercial_tender
from scraper.config import CIVICINFO_BASE_URL, CIVICINFO_BIDS_URL, REQUEST_DELAY_SECONDS, USER_AGENT
from scraper.models import CommercialTender
from scraper.utils import clean_text

CIVICINFO_BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "Referer": f"{CIVICINFO_BASE_URL}/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

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


def _fetch_with_curl_cffi(url: str, params: dict[str, str] | None = None) -> tuple[int, str]:
    from curl_cffi import requests as curl_requests

    response = curl_requests.get(
        url,
        params=params,
        impersonate="chrome120",
        headers={
            "Referer": CIVICINFO_BROWSER_HEADERS["Referer"],
            "Accept-Language": CIVICINFO_BROWSER_HEADERS["Accept-Language"],
        },
        timeout=60,
    )
    return response.status_code, response.text


def fetch_civicinfo_html(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Fetch CivicInfo HTML; plain requests first, then curl_cffi if blocked."""
    time.sleep(REQUEST_DELAY_SECONDS)
    response = session.get(url, params=params, headers=CIVICINFO_BROWSER_HEADERS, timeout=60)
    if response.status_code == 200 and "just a moment" not in response.text[:2000].lower():
        return response.status_code, response.text

    if response.status_code == 403:
        print("[CivicInfo] Got 403 from requests; retrying with curl_cffi (chrome120)...")
        try:
            return _fetch_with_curl_cffi(url, params)
        except ImportError as exc:
            raise RuntimeError(
                "CivicInfo blocked requests (403) and curl_cffi is not installed"
            ) from exc

    response.raise_for_status()
    return response.status_code, response.text


def scrape_civicinfo_commercial(session: requests.Session) -> list[CommercialTender]:
    print("[CivicInfo] Fetching municipal bids...")
    status_code, html = fetch_civicinfo_html(
        session,
        CIVICINFO_BIDS_URL,
        params={"per_page": "100"},
    )
    print(f"[CivicInfo] Listing page HTTP {status_code}")
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
