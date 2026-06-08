from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import (
    BCBID_BASE_URL,
    BCBID_BROWSE_URL,
    BCBID_DETAIL_URL_TEMPLATE,
)
from scraper.models import Tender
from scraper.utils import (
    clean_text,
    extract_label_value_map,
    is_browser_check,
    load_netscape_cookies,
    matches_target_category,
    polite_get,
    polite_post,
)


def _parse_grid_row(row) -> dict[str, str] | None:
    tender_id = row.get("data-id")
    if not tender_id:
        return None

    cells = row.find_all("td", attrs={"data-iv-role": "cell"})
    if len(cells) < 11:
        return None

    link = cells[1].find("a", href=True)
    detail_path = link["href"] if link else f"/page.aspx/en/bpm/process_manage_extranet/{tender_id}"

    return {
        "tender_id": tender_id,
        "status": clean_text(cells[0].get_text(" ", strip=True)),
        "title": clean_text(cells[2].get_text(" ", strip=True)),
        "commodities": clean_text(cells[3].get_text(" ", strip=True)),
        "type": clean_text(cells[4].get_text(" ", strip=True)),
        "posted_date": clean_text(cells[5].get_text(" ", strip=True)),
        "closing_date": clean_text(cells[6].get_text(" ", strip=True)),
        "organization": clean_text(cells[10].get_text(" ", strip=True)),
        "url": urljoin(BCBID_BASE_URL, detail_path),
    }


def _extract_form_payload(soup: BeautifulSoup) -> dict[str, str]:
    payload: dict[str, str] = {}
    form = soup.find("form", id="mainForm")
    if not form:
        return payload

    for element in form.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name or element.get("type") == "submit":
            continue
        if element.name == "select":
            selected = element.find("option", selected=True) or element.find("option")
            payload[name] = selected["value"] if selected and selected.has_attr("value") else ""
        elif element.get("type") in {"checkbox", "radio"}:
            if element.has_attr("checked"):
                payload[name] = element.get("value", "on")
        else:
            payload[name] = element.get("value", "")

    return payload


def _iter_browse_pages(session: requests.Session) -> Iterator[BeautifulSoup]:
    print("[BC Bid] Fetching opportunities listing...")
    response = polite_get(session, BCBID_BROWSE_URL)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    if is_browser_check(soup):
        raise RuntimeError(
            "BC Bid returned a browser check page. Export cookies from your browser after "
            "visiting bcbid.gov.bc.ca and save them to bcbid_cookies.txt (Netscape format), "
            "then run the scraper again."
        )

    yield soup

    page = 1
    while True:
        pager_next = soup.find("button", attrs={"aria-label": re.compile(r"Next page", re.I)})
        if not pager_next or pager_next.get("aria-disabled") == "true":
            break

        button_name = pager_next.get("name")
        if not button_name:
            break

        payload = _extract_form_payload(soup)
        payload[button_name] = pager_next.get("value", "")
        page += 1
        print(f"[BC Bid] Fetching opportunities page {page}...")
        response = polite_post(session, BCBID_BROWSE_URL, data=payload)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if is_browser_check(soup):
            break
        yield soup


def _parse_detail_page(session: requests.Session, summary: dict[str, str]) -> Tender | None:
    print(f"[BC Bid] Parsing detail: {summary['title'][:70]}")
    response = polite_get(session, summary["url"])
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    if is_browser_check(soup):
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

    estimated_value = ""
    for key, value in labels.items():
        if "estimated" in key.lower() and "value" in key.lower():
            estimated_value = value
            break
        if key.lower().startswith("estimated contract value"):
            estimated_value = value
            break

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
    cookie_file = cookie_path or Path("bcbid_cookies.txt")
    load_netscape_cookies(session, cookie_file)

    summaries: list[dict[str, str]] = []
    for soup in _iter_browse_pages(session):
        grid = soup.find("table", id="body_x_grid_grd")
        if not grid:
            continue
        for row in grid.find_all("tr", attrs={"data-id": True}):
            summary = _parse_grid_row(row)
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
