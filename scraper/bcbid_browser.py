"""Pass BC Bid browser check via headless Chromium and seed curl_cffi session cookies."""

from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup
from curl_cffi import requests

from scraper.config import BCBID_BROWSE_URL
from scraper.utils import is_browser_check

BOOTSTRAP_JS = """
const reset = document.getElementById('body_x_prxFilterBar_x_cmdRazBtn');
if (reset) {
  reset.click();
  await new Promise(r => setTimeout(r, 5000));
}
return document.querySelectorAll('table#body_x_grid_grd tr[data-id]').length;
"""

WAIT_FOR_LISTING = (
    "js:() => document.title.includes('Opportunities') && "
    "document.querySelector('table#body_x_grid_grd tr[data-id]') !== null"
)


def _listing_has_rows(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    if is_browser_check(soup):
        return False
    grid = soup.find("table", id="body_x_grid_grd")
    if grid is None:
        return False
    return len(grid.find_all("tr", attrs={"data-id": True})) > 0


def _cookies_from_storage_state(state: dict | None) -> list[dict]:
    if not state:
        return []
    return state.get("cookies") or []


def _load_cookies_into_session(session: requests.Session, cookies: list[dict]) -> int:
    loaded = 0
    for cookie in cookies:
        domain = cookie.get("domain") or ""
        if "bcbid" not in domain:
            continue
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=domain.lstrip("."),
            path=cookie.get("path", "/"),
        )
        loaded += 1
    return loaded


async def _bootstrap_cookies_async() -> list[dict]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        enable_stealth=True,
        extra_args=["--disable-blink-features=AutomationControlled"],
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for=WAIT_FOR_LISTING,
        js_code=BOOTSTRAP_JS,
        delay_before_return_html=3.0,
        page_timeout=180_000,
        verbose=False,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(BCBID_BROWSE_URL, config=run_config)
        html = (result.html or "") if result.success else ""
        if not result.success:
            raise RuntimeError(result.error_message or "BC Bid browser bootstrap crawl failed")
        if not _listing_has_rows(html):
            final_url = result.redirected_url or BCBID_BROWSE_URL
            raise RuntimeError(
                f"BC Bid browser check not passed (final_url={final_url}, grid_rows=0)"
            )

        state = await crawler.crawler_strategy.export_storage_state()
        return _cookies_from_storage_state(state)


def bootstrap_bcbid_session(session: requests.Session) -> int:
    """Use headless Chromium to pass browser check and copy cookies into curl_cffi session."""
    try:
        import crawl4ai  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "crawl4ai is required for BC Bid browser bootstrap. Install requirements.txt "
            "and run: playwright install chromium"
        ) from exc

    print("[BC Bid] Starting headless browser to pass browser check...")
    cookies = asyncio.run(_bootstrap_cookies_async())
    loaded = _load_cookies_into_session(session, cookies)
    print(f"[BC Bid] Browser bootstrap loaded {loaded} cookies into curl_cffi session")
    if loaded == 0:
        raise RuntimeError("BC Bid browser bootstrap returned no cookies")
    return loaded
