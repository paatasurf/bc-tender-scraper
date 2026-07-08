"""Playwright persistent browser profile for authenticated LinkedIn scraping."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from research.linkedin.paths import BROWSER_PROFILE_DIR
from research.linkedin.scraper.adapter import LinkedInCompanyRecord, _company_to_record
from research.linkedin.session import (
    ProfileExpiredError,
    is_login_url,
    print_profile_refresh_message,
    profile_is_initialized,
)


@asynccontextmanager
async def persistent_browser_context(
    *,
    headless: bool = True,
    profile_dir: str | None = None,
) -> AsyncIterator[Any]:
    from playwright.async_api import async_playwright

    user_data_dir = profile_dir or str(BROWSER_PROFILE_DIR)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _assert_profile_logged_in(page)
            yield page
        finally:
            await context.close()


async def _assert_profile_logged_in(page: Any) -> None:
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1500)
    if is_login_url(page.url):
        raise ProfileExpiredError("LinkedIn profile is not logged in or session expired.")


async def scrape_single_company(
    page: Any,
    url: str,
    *,
    company_name: str | None = None,
) -> LinkedInCompanyRecord:
    from linkedin_scraper import CompanyScraper

    if is_login_url(page.url):
        raise ProfileExpiredError("LinkedIn redirected to login before scrape.")

    scraper = CompanyScraper(page)
    company = await scraper.scrape(url)

    if is_login_url(page.url):
        raise ProfileExpiredError("LinkedIn redirected to login during scrape.")

    record = _company_to_record(company, url=url)
    record.source_fields["fetch_mode"] = "playwright_persistent_profile"
    if company_name and not record.company_name:
        record.company_name = company_name
    return record


def scrape_single_company_sync(
    page_factory,
    url: str,
    *,
    company_name: str | None = None,
    headless: bool = True,
) -> LinkedInCompanyRecord:
    """Scrape one URL using a fresh persistent context (for standalone calls)."""

    async def _run() -> LinkedInCompanyRecord:
        async with persistent_browser_context(headless=headless) as page:
            return await scrape_single_company(page, url, company_name=company_name)

    return asyncio.run(_run())


def profile_ready() -> bool:
    return profile_is_initialized()
