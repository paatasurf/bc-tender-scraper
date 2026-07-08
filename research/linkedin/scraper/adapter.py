"""LinkedIn company scraper adapter — Playwright storageState + linkedin_scraper."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from research.linkedin.session import (
    SessionExpiredError,
    is_login_url,
    print_session_refresh_message,
    require_session_path,
    resolve_session_path,
)


@dataclass
class LinkedInCompanyRecord:
    company_name: str | None = None
    linkedin_company_url: str = ""
    website: str | None = None
    industry: str | None = None
    headquarters: str | None = None
    company_size: str | None = None
    specialties: str | None = None
    founded: str | None = None
    description: str | None = None
    location: str | None = None
    scrape_status: str = "pending"
    scrape_error: str | None = None
    scraped_at: str | None = None
    source_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _company_to_record(company: Any, *, url: str) -> LinkedInCompanyRecord:
    specialties = getattr(company, "specialties", None)
    if isinstance(specialties, list):
        specialties = ", ".join(specialties)
    return LinkedInCompanyRecord(
        company_name=getattr(company, "name", None),
        linkedin_company_url=getattr(company, "linkedin_url", None) or url,
        website=getattr(company, "website", None),
        industry=getattr(company, "industry", None),
        headquarters=getattr(company, "headquarters", None),
        company_size=getattr(company, "company_size", None),
        specialties=specialties,
        founded=getattr(company, "founded", None),
        description=getattr(company, "about_us", None),
        location=getattr(company, "headquarters", None),
        scrape_status="ok",
        scraped_at=datetime.now(timezone.utc).isoformat(),
        source_fields={
            "fetch_mode": "playwright_storage_state",
            "company_type": getattr(company, "company_type", None),
            "headcount": getattr(company, "headcount", None),
        },
    )


async def _assert_session_valid(page: Any) -> None:
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1500)
    if is_login_url(page.url):
        raise SessionExpiredError("LinkedIn redirected to login — session expired.")


async def _scrape_urls_async(
    urls: list[str],
    *,
    session_path: str,
    headless: bool = True,
    delay_seconds: float = 2.0,
) -> list[LinkedInCompanyRecord]:
    from linkedin_scraper import CompanyScraper
    from playwright.async_api import async_playwright

    records: list[LinkedInCompanyRecord] = []
    session_expired = False

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=session_path)
        page = await context.new_page()

        try:
            await _assert_session_valid(page)
        except SessionExpiredError:
            await browser.close()
            print_session_refresh_message()
            raise

        scraper = CompanyScraper(page)
        total = len(urls)

        for index, url in enumerate(urls, start=1):
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            try:
                if session_expired or is_login_url(page.url):
                    raise SessionExpiredError("LinkedIn session expired mid-batch.")

                company = await scraper.scrape(url)
                if is_login_url(page.url):
                    session_expired = True
                    raise SessionExpiredError("LinkedIn redirected to login during scrape.")

                records.append(_company_to_record(company, url=url))
            except SessionExpiredError:
                print_session_refresh_message()
                raise
            except Exception as exc:
                error_text = str(exc).lower()
                if "login" in error_text or "authwall" in error_text:
                    print_session_refresh_message()
                    raise SessionExpiredError("LinkedIn session expired during scrape.") from exc
                records.append(
                    LinkedInCompanyRecord(
                        linkedin_company_url=url,
                        scrape_status="error",
                        scrape_error=str(exc)[:500],
                        scraped_at=datetime.now(timezone.utc).isoformat(),
                        source_fields={"fetch_mode": "playwright_storage_state"},
                    )
                )

            if index % 25 == 0 or index == total:
                ok = sum(1 for r in records if r.scrape_status == "ok")
                print(
                    f"[session-scrape] {index}/{total} ok={ok} err={index - ok}",
                    flush=True,
                )
            if index < total and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

        await browser.close()

    return records


def scrape_company_urls(
    urls: list[str],
    *,
    session_path: str | None = None,
    headless: bool = True,
    delay_seconds: float = 2.0,
) -> list[LinkedInCompanyRecord]:
    """Scrape LinkedIn company pages using Playwright storageState session."""
    resolved = resolve_session_path(session_path)
    if not resolved:
        raise RuntimeError(
            "LinkedIn session required. Run research/linkedin/scripts/create_session.py "
            "or set LINKEDIN_SESSION_PATH."
        )
    session_path = require_session_path(resolved)

    try:
        return asyncio.run(
            _scrape_urls_async(
                urls,
                session_path=session_path,
                headless=headless,
                delay_seconds=delay_seconds,
            )
        )
    except SessionExpiredError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "storage" in message or "session" in message or "login" in message:
            print_session_refresh_message()
            raise SessionExpiredError("LinkedIn session could not be loaded.") from exc
        raise
