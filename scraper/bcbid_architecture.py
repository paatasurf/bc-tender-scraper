from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from scraper.bcbid_common import (
    _grid_page_state,
    _resolve_bcbid_cookie_file,
    extract_estimated_value,
    parse_grid_row,
)
from scraper.config import (
    ARCHITECTURE_CATEGORY_LABEL,
    ARCH_TENDERS_CSV,
    BCBID_BASE_URL,
    BCBID_BROWSE_URL,
    REQUEST_DELAY_SECONDS,
    USER_AGENT,
)
from scraper.models import ArchTender
from scraper.utils import (
    clean_text,
    extract_label_value_map,
    is_browser_check,
    save_csv_rows,
)

STEALTH_INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"


def is_architecture_engineering_listing_hint(*parts: str) -> bool:
    blob = " ".join(clean_text(part) for part in parts if part).lower()
    if ARCHITECTURE_CATEGORY_LABEL.lower() in blob:
        return True
    if "architect" in blob:
        return True
    return "engineering" in blob or "engineer" in blob


def is_architecture_engineering_category(*parts: str) -> bool:
    blob = " ".join(clean_text(part) for part in parts if part).lower()
    if ARCHITECTURE_CATEGORY_LABEL.lower() in blob:
        return True
    return "architect" in blob and ("engineering" in blob or "engineer" in blob)


def _page_soup(page: Page) -> BeautifulSoup:
    return BeautifulSoup(page.content(), "html.parser")


def _playwright_cookies_from_file(cookie_file: Path) -> list[dict[str, str | bool]]:
    if not cookie_file.exists():
        return []

    cookies: list[dict[str, str | bool]] = []
    for line in cookie_file.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, path, secure, _expires, name, value = parts
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure.upper() == "TRUE",
            }
        )
    return cookies


def _load_playwright_cookies(page: Page, cookie_path: Path | None = None) -> None:
    cookie_file = cookie_path or _resolve_bcbid_cookie_file()
    cookies = _playwright_cookies_from_file(cookie_file)
    if not cookies:
        return

    page.goto(BCBID_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.context.add_cookies(cookies)


def _attempt_browser_check(page: Page) -> None:
    try:
        page.wait_for_function("() => typeof window.ivCaptcha !== 'undefined'", timeout=10_000)
        page.evaluate("() => window.ivCaptcha.solve && window.ivCaptcha.solve()")
    except PlaywrightTimeoutError:
        return


def _wait_for_opportunities_page(page: Page) -> None:
    _attempt_browser_check(page)
    page.wait_for_function(
        """() => {
            const title = document.title || '';
            if (title.toLowerCase().includes('browser check')) return false;
            return document.getElementById('body_x_grid_grd') !== null;
        }""",
        timeout=120_000,
    )


def _wait_for_detail_page(page: Page) -> None:
    _attempt_browser_check(page)
    page.wait_for_function(
        """() => {
            const title = document.title || '';
            if (title.toLowerCase().includes('browser check')) return false;
            return document.querySelector('dl, .field') !== null;
        }""",
        timeout=120_000,
    )


def _click_next_browse_page(page: Page, next_page_index: int) -> None:
    page.locator('button[aria-label*="Next page" i]').click()
    page.wait_for_function(
        f"""() => {{
            const field = document.querySelector('input[name="hdnCurrentPageIndexbody_x_grid_grd"]');
            return field && parseInt(field.value, 10) === {next_page_index};
        }}""",
        timeout=60_000,
    )


def iter_browse_pages_playwright(
    page: Page,
    log_prefix: str = "[BC Bid Architecture]",
) -> Iterator[BeautifulSoup]:
    print(f"{log_prefix} Fetching opportunities listing...")
    page.goto(BCBID_BROWSE_URL, wait_until="domcontentloaded", timeout=60_000)
    _wait_for_opportunities_page(page)
    soup = _page_soup(page)

    if is_browser_check(soup):
        raise RuntimeError(
            "BC Bid returned a browser check page. The Playwright browser could not pass the check."
        )

    yield soup

    page_number = 1
    while True:
        soup = _page_soup(page)
        pager_next = soup.find("button", attrs={"aria-label": re.compile(r"Next page", re.I)})
        if not pager_next or pager_next.get("aria-disabled") == "true":
            break

        current_index, max_page_index = _grid_page_state(soup)
        if current_index is None or max_page_index is None or current_index >= max_page_index:
            break

        next_page_index = current_index + 1
        page_number += 1
        print(f"{log_prefix} Fetching opportunities page {page_number}...")
        time.sleep(REQUEST_DELAY_SECONDS)
        _click_next_browse_page(page, next_page_index)
        _wait_for_opportunities_page(page)
        soup = _page_soup(page)
        if is_browser_check(soup):
            break
        yield soup


def parse_architecture_detail(page: Page, summary: dict[str, str]) -> ArchTender | None:
    print(f"[BC Bid Architecture] Parsing detail: {summary['title'][:70]}")
    time.sleep(REQUEST_DELAY_SECONDS)
    page.goto(summary["url"], wait_until="domcontentloaded", timeout=60_000)
    _wait_for_detail_page(page)
    soup = _page_soup(page)

    if is_browser_check(soup):
        return None

    labels = extract_label_value_map(soup)
    title = labels.get("Opportunity Description", summary["title"])
    company = summary["organization"] or labels.get("Issued by", "")
    category = labels.get("Industry Category", summary["commodities"] or summary["type"])
    deadline = labels.get("Closing Date and Time", summary["closing_date"])
    status = summary["status"] or labels.get("Status", "Open")
    value = extract_estimated_value(labels)

    if not is_architecture_engineering_category(category, summary["commodities"], summary["type"], title):
        return None

    tender_id = labels.get("Opportunity ID", summary["tender_id"])
    if not tender_id:
        match = re.search(r"/(\d+)\s*$", summary["url"])
        tender_id = match.group(1) if match else summary["tender_id"]

    return ArchTender(
        title=title,
        company=company,
        value=value,
        deadline=deadline,
        status=status,
        category=category or ARCHITECTURE_CATEGORY_LABEL,
        url=summary["url"],
        tender_id=tender_id,
    )


def save_arch_tenders(tenders: list[ArchTender], csv_path: str = ARCH_TENDERS_CSV) -> None:
    fieldnames = ["title", "company", "value", "deadline", "status", "category", "url", "tender_id"]
    save_csv_rows((tender.to_dict() for tender in tenders), csv_path, fieldnames)


def scrape_bcbid_architecture_tenders(
    session: requests.Session,
    cookie_path: Path | None = None,
) -> list[ArchTender]:
    del session

    summaries: list[dict[str, str]] = []
    tenders: list[ArchTender] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="en-CA",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "en-CA,en;q=0.9"},
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.new_page()
        try:
            _load_playwright_cookies(page, cookie_path)

            for soup in iter_browse_pages_playwright(page):
                grid = soup.find("table", id="body_x_grid_grd")
                if not grid:
                    continue
                for row in grid.find_all("tr", attrs={"data-id": True}):
                    summary = parse_grid_row(row)
                    if summary and is_architecture_engineering_listing_hint(
                        summary["commodities"], summary["title"], summary["type"]
                    ):
                        summaries.append(summary)

            print(f"[BC Bid Architecture] Found {len(summaries)} architecture & engineering opportunities")

            for index, summary in enumerate(summaries, start=1):
                print(f"[BC Bid Architecture] Checking match {index}/{len(summaries)}")
                tender = parse_architecture_detail(page, summary)
                if tender:
                    tenders.append(tender)
        finally:
            browser.close()

    save_arch_tenders(tenders)
    print(f"[BC Bid Architecture] Saved {len(tenders)} tenders to {ARCH_TENDERS_CSV}")
    return tenders
