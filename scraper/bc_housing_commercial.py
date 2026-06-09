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
    MERX_BASE_URL,
    MERX_OPEN_LIST_PATH,
)
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


def _parse_merx_listing_link(link, base_url: str) -> CommercialTender | None:
    buyer_el = link.select_one(".buyer-name")
    buyer = clean_text(buyer_el.get_text(" ", strip=True) if buyer_el else "")
    if not BC_HOUSING_BUYER_PATTERN.search(buyer):
        return None

    title_el = link.select_one(".rowTitle")
    title = clean_text(title_el.get_text(" ", strip=True) if title_el else "")
    if not title:
        return None

    closing_el = link.select_one(".closingDate .dateValue")
    remaining_el = link.select_one(".timeRemaining")
    tender_id_el = link.select_one(".accessibility-hidden")
    href = link.get("href")
    if not href:
        return None

    status = "Open"
    if remaining_el:
        remaining = clean_text(remaining_el.get_text(" ", strip=True))
        if remaining:
            status = remaining

    return make_commercial_tender(
        title=title,
        company=buyer,
        url=urljoin(base_url, href),
        source="bc_housing",
        deadline=clean_text(closing_el.get_text(" ", strip=True) if closing_el else ""),
        status=status,
        tender_id=clean_text(tender_id_el.get_text(" ", strip=True) if tender_id_el else ""),
    )


def _scrape_merx_bc_housing(session: requests.Session) -> list[CommercialTender]:
    tenders: list[CommercialTender] = []
    seen_urls: set[str] = set()
    page = 1

    while page <= 5:
        params: dict[str, str] = {"location": MERX_ARCH_BC_LOCATION}
        if page > 1:
            params["pageNumber"] = str(page)

        list_url = urljoin(MERX_BASE_URL, MERX_OPEN_LIST_PATH)
        print(f"[BC Housing] Scanning MERX BC listings page {page}...")
        response = polite_get(session, list_url, params=params)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.select("a.solicitation-link")
        if not links:
            break

        for link in links:
            tender = _parse_merx_listing_link(link, MERX_BASE_URL)
            if tender and tender.url not in seen_urls:
                seen_urls.add(tender.url)
                tenders.append(tender)

        next_link = soup.find("link", rel="next")
        if not next_link:
            break
        page += 1

    return tenders


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
