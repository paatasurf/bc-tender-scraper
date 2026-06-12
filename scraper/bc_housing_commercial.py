from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.commercial_common import make_commercial_tender
from scraper.config import (
    BC_HOUSING_BASE_URL,
    BC_HOUSING_ORG_NAME,
    BC_HOUSING_PROJECTS_URL,
    MERX_ARCH_BC_LOCATION,
    MERX_OPEN_LIST_PATH,
)
from scraper.merx_common import iter_merx_listing_pages
from scraper.models import CommercialTender
from scraper.utils import clean_text, polite_get

BC_HOUSING_BUYER_PATTERN = re.compile(r"bc\s*housing", re.I)


def _parse_projects_page(soup: BeautifulSoup) -> list[CommercialTender]:
    tenders: list[CommercialTender] = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/projects-partners/projects/" not in href:
            continue

        title = clean_text(link.get_text(" ", strip=True))
        if not title or title.lower() in {"show more", "projects in development"}:
            continue

        url = href if href.startswith("http") else urljoin(BC_HOUSING_BASE_URL, href)
        slug = href.rstrip("/").split("/")[-1]
        tenders.append(
            make_commercial_tender(
                title=title,
                company=BC_HOUSING_ORG_NAME,
                url=url,
                source="bc_housing",
                status="In Development",
                tender_id=slug,
            )
        )
    return tenders


def _bc_housing_listing_filter(listing: dict[str, str]) -> bool:
    return bool(BC_HOUSING_BUYER_PATTERN.search(listing.get("organization", "")))


def _listing_to_commercial_tender(listing: dict[str, str]) -> CommercialTender:
    return make_commercial_tender(
        title=listing["title"],
        company=listing["organization"],
        url=listing["url"],
        source="bc_housing",
        deadline=listing["closing_date"],
        status=listing["status"],
        tender_id=listing["tender_id"],
    )


def _scrape_merx_bc_housing(session: requests.Session) -> list[CommercialTender]:
    params = {"location": MERX_ARCH_BC_LOCATION}
    listings = iter_merx_listing_pages(
        session,
        MERX_OPEN_LIST_PATH,
        params,
        log_prefix="[BC Housing]",
        row_filter=_bc_housing_listing_filter,
    )
    return [_listing_to_commercial_tender(listing) for listing in listings]


def scrape_bc_housing_commercial(session: requests.Session) -> list[CommercialTender]:
    print("[BC Housing] Fetching projects in development...")
    response = polite_get(session, BC_HOUSING_PROJECTS_URL)
    response.raise_for_status()
    project_tenders = _parse_projects_page(BeautifulSoup(response.text, "html.parser"))

    merx_tenders = _scrape_merx_bc_housing(session)

    seen_urls = {tender.url for tender in project_tenders}
    merged = list(project_tenders)
    for tender in merx_tenders:
        if tender.url not in seen_urls:
            seen_urls.add(tender.url)
            merged.append(tender)

    print(f"[BC Housing] Found {len(merged)} commercial opportunities")
    return merged
