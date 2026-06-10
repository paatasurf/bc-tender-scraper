from __future__ import annotations

import asyncio
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchCompany
from pipeline.company_intelligence import _batch_limit
from pipeline.scrape_arch_houzz import _printable

DEFAULT_AIBC_BATCH_LIMIT = 25
REQUEST_DELAY_SECONDS = 3.0
REGISTER_URL = "https://aibc.ca/resources/online-directory/"

# Fills the AIBC register search form (Gravity Forms): picks "Firm Registrant",
# enters the firm name, waits for the Cloudflare Turnstile token, then submits.
SEARCH_JS_TEMPLATE = """
(async () => {
  const radio = document.querySelector('input[name="input_1"][value="FR"]');
  if (radio) { radio.click(); radio.dispatchEvent(new Event('change', {bubbles: true})); }
  await new Promise(r => setTimeout(r, 1500));
  const firm = document.querySelector('#input_6_4');
  if (firm) {
    firm.removeAttribute('disabled');
    firm.value = %NAME%;
    firm.dispatchEvent(new Event('input', {bubbles: true}));
    firm.dispatchEvent(new Event('change', {bubbles: true}));
  }
  for (let i = 0; i < 50; i++) {
    const t = document.querySelector('input[name="cf-turnstile-response"]');
    if (t && t.value && t.value.length > 10) break;
    await new Promise(r => setTimeout(r, 500));
  }
  const btn = document.querySelector('#gform_submit_button_6');
  if (btn) btn.click();
  await new Promise(r => setTimeout(r, 8000));
})();
"""

STATUS_RE = re.compile(r"\b(Active|Suspended|Cancelled)\b")


def _extract_status(html: str, name: str) -> str:
    """Find the firm's row in the register results and read its status."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) > 2]
    if not tokens:
        return ""
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        text = re.sub(r"<[^>]+>", " ", row)
        lowered = text.lower()
        hits = sum(1 for t in tokens if t in lowered)
        if hits / len(tokens) >= 0.6:
            match = STATUS_RE.search(text)
            if match:
                return match.group(1)
    return ""


async def _search_batch(session: Session, companies: list[ArchCompany]) -> int:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    import json as _json

    browser_config = BrowserConfig(headless=True, verbose=False)
    found = 0
    blocked_in_a_row = 0

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for index, company in enumerate(companies, start=1):
            print(f"[ArchCompanies] AIBC {index}/{len(companies)}: {_printable(company.name[:70])}")
            config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                js_code=SEARCH_JS_TEMPLATE.replace("%NAME%", _json.dumps(company.name)),
                delay_before_return_html=12.0,
                page_timeout=120_000,
                verbose=False,
            )
            try:
                result = await crawler.arun(REGISTER_URL, config=config)
                html = result.html or "" if result.success else ""
                status = _extract_status(html, company.name)
                if status:
                    company.aibc_status = status
                    found += 1
                    blocked_in_a_row = 0
                elif "cf-turnstile" in html and "<table" not in html.lower():
                    # The Turnstile challenge was never solved; the form did not submit.
                    blocked_in_a_row += 1
                    if blocked_in_a_row >= 3:
                        print(
                            "[ArchCompanies] AIBC register blocked by Cloudflare Turnstile "
                            "3 times in a row; stopping this run."
                        )
                        break
                session.commit()
            except Exception as exc:
                session.rollback()
                print(f"[ArchCompanies] AIBC search failed for {_printable(company.name[:50])}: {exc}")

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    return found


def scrape_arch_aibc(session: Session) -> int:
    """Look up each arch company in the AIBC register (Firm Registrants) with
    Crawl4AI and store the registration status in aibc_status.

    Best effort: the register sits behind a Cloudflare Turnstile challenge; when
    the challenge is not auto-solved the run stops early and logs the block."""
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        print("[ArchCompanies] Skipping AIBC scrape: crawl4ai is not installed.")
        return 0

    limit = _batch_limit("ARCH_COMPANY_AIBC_MAX_PER_RUN", DEFAULT_AIBC_BATCH_LIMIT)
    companies = session.scalars(
        select(ArchCompany)
        .where(ArchCompany.aibc_status == "", ArchCompany.name != "")
        .order_by(ArchCompany.total_value.desc())
        .limit(limit)
    ).all()

    print(f"[ArchCompanies] AIBC register: {len(companies)} firms queued (max {limit})")
    if not companies:
        return 0

    try:
        found = asyncio.run(_search_batch(session, companies))
    except Exception as exc:
        print(f"[ArchCompanies] AIBC scrape aborted: {exc}")
        return 0

    print(f"[ArchCompanies] AIBC register complete: {found} firms verified")
    return found
