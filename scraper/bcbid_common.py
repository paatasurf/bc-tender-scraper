from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests

from scraper.bcbid_auth import (
    BcbidSessionExpiredError,
    handle_bcbid_auth_failure,
    is_bcbid_auth_failure,
)
from scraper.bcbid_http import bcbid_get, bcbid_post
from scraper.config import BCBID_BASE_URL, BCBID_BROWSE_URL
from scraper.utils import clean_text


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


def _grid_page_state(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    current_field = soup.find("input", attrs={"name": "hdnCurrentPageIndexbody_x_grid_grd"})
    max_field = soup.find("input", attrs={"name": "maxpageindexbody_x_grid_grd"})
    current = int(current_field["value"]) if current_field and current_field.get("value", "").isdigit() else None
    max_page = int(max_field["value"]) if max_field and max_field.get("value", "").isdigit() else None
    return current, max_page


def _build_grid_page_payload(soup: BeautifulSoup, next_page_index: int) -> dict[str, str]:
    payload = extract_form_payload(soup)
    payload["hdnCurrentPageIndexbody_x_grid_grd"] = str(next_page_index)
    payload["__EVENTTARGET"] = "body_x_grid_grd"
    payload["__EVENTARGUMENT"] = f"Page|{next_page_index + 1}"
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


def _grid_row_count(soup: BeautifulSoup) -> int:
    grid = soup.find("table", id="body_x_grid_grd")
    if not grid:
        return 0
    return len(grid.find_all("tr", attrs={"data-id": True}))


def _log_listing_response(response: requests.Response, soup: BeautifulSoup, *, label: str) -> None:
    title = soup.title.get_text(strip=True) if soup.title else ""
    auth_failure = is_bcbid_auth_failure(soup, html=response.text)
    rows = _grid_row_count(soup)
    print(
        f"[BC Bid] {label}: HTTP {response.status_code}, "
        f"final_url={response.url}, title={title!r}, "
        f"auth_or_login_page={auth_failure}, grid_rows={rows}"
    )


def _build_reset_filters_payload(soup: BeautifulSoup) -> dict[str, str]:
    payload = extract_form_payload(soup)
    reset_name = "body:x:prxFilterBar:x:cmdRazBtn"
    payload[reset_name] = "Reset"
    for key in list(payload.keys()):
        lowered = key.lower()
        if "status" in lowered and key != reset_name:
            payload[key] = ""
    return payload


def _fetch_unfiltered_browse_listing(session: requests.Session, log_prefix: str) -> BeautifulSoup:
    """Load the public browse page and click Reset so no status filter hides open rows."""
    print(f"{log_prefix} Fetching opportunities listing...")
    response = bcbid_get(session, BCBID_BROWSE_URL)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    _log_listing_response(response, soup, label="Initial listing GET")

    if is_bcbid_auth_failure(soup, html=response.text):
        handle_bcbid_auth_failure(soup, html=response.text)
        raise BcbidSessionExpiredError("listing page auth failure on initial GET")

    if _grid_row_count(soup) == 0:
        print(f"{log_prefix} Grid empty on first load — posting Reset to clear filters...")
    else:
        print(f"{log_prefix} Posting Reset to ensure no restrictive status filter is applied...")

    reset_payload = _build_reset_filters_payload(soup)
    reset_response = bcbid_post(session, BCBID_BROWSE_URL, data=reset_payload)
    reset_response.raise_for_status()
    reset_soup = BeautifulSoup(reset_response.text, "html.parser")
    _log_listing_response(reset_response, reset_soup, label="After Reset POST")

    if is_bcbid_auth_failure(reset_soup, html=reset_response.text):
        handle_bcbid_auth_failure(reset_soup, html=reset_response.text)
        raise BcbidSessionExpiredError("listing page auth failure after Reset")

    grid = reset_soup.find("table", id="body_x_grid_grd")
    if grid is None:
        handle_bcbid_auth_failure(reset_soup, html=reset_response.text)
        raise BcbidSessionExpiredError("opportunities grid missing after Reset")

    if _grid_row_count(reset_soup) == 0:
        print(
            f"{log_prefix} WARNING: grid present but 0 rows after Reset "
            "(valid session but no open opportunities returned)"
        )
    return reset_soup


def iter_browse_pages(session: requests.Session, log_prefix: str = "[BC Bid]") -> Iterator[BeautifulSoup]:
    soup = _fetch_unfiltered_browse_listing(session, log_prefix)

    yield soup

    page = 1
    while True:
        pager_next = soup.find("button", attrs={"aria-label": re.compile(r"Next page", re.I)})
        if not pager_next or pager_next.get("aria-disabled") == "true":
            break

        current_index, max_page_index = _grid_page_state(soup)
        if current_index is None or max_page_index is None or current_index >= max_page_index:
            break

        next_page_index = current_index + 1
        page += 1
        print(f"{log_prefix} Fetching opportunities page {page}...")
        payload = _build_grid_page_payload(soup, next_page_index)
        response = bcbid_post(session, BCBID_BROWSE_URL, data=payload)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if is_bcbid_auth_failure(soup, html=response.text):
            handle_bcbid_auth_failure(soup, html=response.text)
            break
        if soup.find("table", id="body_x_grid_grd") is None:
            handle_bcbid_auth_failure(soup, html=response.text)
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
