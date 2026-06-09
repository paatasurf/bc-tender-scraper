from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.commercial_common import make_commercial_tender
from scraper.config import BIDCENTRAL_BOBS_URL, BIDCENTRAL_SCIP_URL
from scraper.models import CommercialTender
from scraper.utils import clean_text, polite_get

PROJECT_ID_PATTERN = re.compile(r"\((20\d{2}-\d{6})\)")
CLOSING_PATTERN = re.compile(
    r"Project Closes On:\s*(.+?)(?:\s+pt)?$",
    re.I,
)


def _parse_scip_rows(soup: BeautifulSoup) -> list[CommercialTender]:
    tenders: list[CommercialTender] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        project_number = clean_text(cells[0].get_text(" ", strip=True))
        title_cell = clean_text(cells[1].get_text(" ", strip=True))
        owner = clean_text(cells[2].get_text(" ", strip=True))
        status = clean_text(cells[3].get_text(" ", strip=True))
        deadline = clean_text(cells[4].get_text(" ", strip=True))
        if status.lower() != "open":
            continue

        link = row.find("a", href=True)
        href = link["href"] if link else ""
        url = href if href.startswith("http") else urljoin("https://secure.bidcentral.ca", href)
        if not url:
            url = f"{BIDCENTRAL_SCIP_URL}#{project_number}"

        title = title_cell.split("Contract No:")[0].strip() or title_cell
        tenders.append(
            make_commercial_tender(
                title=title,
                company=owner,
                url=url,
                source="bidcentral",
                deadline=deadline,
                status=status,
                tender_id=project_number,
            )
        )
    return tenders


def _parse_bobs_open_cards(soup: BeautifulSoup) -> list[CommercialTender]:
    tenders: list[CommercialTender] = []

    for card in soup.select("div.card.mb-3"):
        if not card.find("a", href=lambda href: href and "bobs-wizard-custom.php" in href):
            continue
        title_el = card.find("h5", class_="card-title")
        if not title_el:
            continue

        raw_title = clean_text(title_el.get_text(" ", strip=True))
        match = PROJECT_ID_PATTERN.search(raw_title)
        tender_id = match.group(1) if match else ""
        title = PROJECT_ID_PATTERN.sub("", raw_title).strip() or raw_title

        closing_match = CLOSING_PATTERN.search(card.get_text(" ", strip=True))
        deadline = clean_text(closing_match.group(1)) if closing_match else ""

        doc_link = card.find("a", href=lambda href: href and "secure.bidcentral.ca/project/info" in href)
        if doc_link:
            url = doc_link["href"]
        elif tender_id:
            url = f"{BIDCENTRAL_BOBS_URL}#project-{tender_id}"
        else:
            continue

        tenders.append(
            make_commercial_tender(
                title=title,
                company="BidCentral BOBS",
                url=url,
                source="bidcentral",
                deadline=deadline,
                status="Open",
                tender_id=tender_id,
            )
        )
    return tenders


def scrape_bidcentral_commercial(session: requests.Session) -> list[CommercialTender]:
    print("[BidCentral] Fetching SCIP open projects...")
    scip_response = polite_get(session, BIDCENTRAL_SCIP_URL)
    scip_response.raise_for_status()
    scip_tenders = _parse_scip_rows(BeautifulSoup(scip_response.text, "html.parser"))

    print("[BidCentral] Fetching BOBS open projects...")
    bobs_response = polite_get(session, BIDCENTRAL_BOBS_URL)
    bobs_response.raise_for_status()
    bobs_tenders = _parse_bobs_open_cards(BeautifulSoup(bobs_response.text, "html.parser"))

    seen_urls = set()
    merged: list[CommercialTender] = []
    for tender in scip_tenders + bobs_tenders:
        if tender.url in seen_urls:
            continue
        seen_urls.add(tender.url)
        merged.append(tender)

    print(f"[BidCentral] Found {len(merged)} commercial opportunities")
    return merged
