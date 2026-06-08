from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import BCBID_BASE_URL, BCBID_BROWSE_URL
from scraper.utils import clean_text, is_browser_check, load_netscape_cookies, polite_get, polite_post


def _resolve_bcbid_cookie_file() -> Path:
    cookie_file = Path("bcbid_cookies.txt")
    if cookie_file.exists():
        return cookie_file
    content = os.environ.get("BCBID_COOKIES_CONTENT")
    if not content:
        return cookie_file
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="bcbid_cookies_")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Path(path)


def extract_form_payload(soup: BeautifulSoup) -> dict[str, str]:
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


def parse_grid_row(row) -> dict[str, str] | None:
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


def iter_browse_pages(session: requests.Session, log_prefix: str = "[BC Bid]") -> Iterator[BeautifulSoup]:
    load_netscape_cookies(session, _resolve_bcbid_cookie_file())
    print(f"{log_prefix} Fetching opportunities listing...")
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

        payload = extract_form_payload(soup)
        payload[button_name] = pager_next.get("value", "")
        page += 1
        print(f"{log_prefix} Fetching opportunities page {page}...")
        response = polite_post(session, BCBID_BROWSE_URL, data=payload)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if is_browser_check(soup):
            break
        yield soup


def extract_estimated_value(labels: dict[str, str]) -> str:
    for key, value in labels.items():
        lowered = key.lower()
        if "estimated" in lowered and "value" in lowered:
            return value
        if lowered.startswith("estimated contract value"):
            return value
    return ""
