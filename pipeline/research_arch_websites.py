from __future__ import annotations

import time
import urllib.parse
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key
from db.models import ArchCompany
from pipeline.company_intelligence import (
    MAX_LIST_ITEMS,
    _batch_limit,
    _extract_json,
)
from pipeline.scrape_arch_houzz import _printable

WEBSITE_RESEARCH_MODEL = "claude-haiku-4-5-20251001"

DEFAULT_WEBSITE_BATCH_LIMIT = 25
REQUEST_DELAY_SECONDS = 15.0
MAX_SEARCHES_PER_FIRM = 2
MAX_RESPONSE_TOKENS = 512


def _domain_from_website(website: str) -> str:
    """Normalize a stored website value ("childesign.com", "https://www.x.ca/about")
    to a bare domain usable in the web search allowed_domains filter."""
    value = website.strip()
    if not value:
        return ""
    if "//" not in value:
        value = f"https://{value}"
    host = urllib.parse.urlparse(value).netloc.lower()
    return host.removeprefix("www.")


def _build_prompt(company: ArchCompany, domain: str) -> str:
    return f"""Scan {domain} for BC architecture firm "{company.name}".
Check homepage and one projects/portfolio page only — do not crawl the full site.

Count BC/Canada projects only. Return JSON only:
{{"projects_count": <int|null>, "specializations": ["..."], "service_areas": ["..."], "notable": ["..."]}}"""


def _research_firm(
    client: anthropic.Anthropic, company: ArchCompany, domain: str
) -> dict[str, Any]:
    response = client.messages.create(
        model=WEBSITE_RESEARCH_MODEL,
        max_tokens=MAX_RESPONSE_TOKENS,
        messages=[{"role": "user", "content": _build_prompt(company, domain)}],
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": MAX_SEARCHES_PER_FIRM,
                "allowed_domains": [domain],
                "user_location": {
                    "type": "approximate",
                    "city": "Vancouver",
                    "region": "British Columbia",
                    "country": "CA",
                },
            }
        ],
    )
    text = "\n".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ValueError("Claude returned no text content")
    return _extract_json(text)


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = [str(v).strip()[:100] for v in values if str(v).strip()]
    return cleaned[:MAX_LIST_ITEMS]


def research_arch_websites(session: Session) -> int:
    """Research each arch company's website sequentially (one firm at a time) via
    Claude web search, with a 15s pause between API calls to stay under rate limits."""
    api_key = get_anthropic_api_key()
    if not api_key:
        print("[ArchCompanies] Skipping website research: ANTHROPIC_API_KEY is not set.")
        return 0

    client = anthropic.Anthropic(api_key=api_key)
    limit = _batch_limit("ARCH_COMPANY_WEBSITE_MAX_PER_RUN", DEFAULT_WEBSITE_BATCH_LIMIT)
    companies = session.scalars(
        select(ArchCompany)
        .where(
            ArchCompany.website != "",
            ArchCompany.website_projects_count.is_(None),
        )
        .order_by(ArchCompany.total_value.desc())
        .limit(limit)
    ).all()

    print(f"[ArchCompanies] Website research: {len(companies)} firms queued (max {limit})")

    researched = 0
    for index, company in enumerate(companies, start=1):
        if index > 1:
            time.sleep(REQUEST_DELAY_SECONDS)

        print(
            f"[ArchCompanies] Website {index}/{len(companies)}: {_printable(company.name[:70])}"
        )
        domain = _domain_from_website(company.website)
        if not domain:
            continue
        try:
            payload = _research_firm(client, company, domain)

            count = payload.get("projects_count")
            company.website_projects_count = int(count) if isinstance(count, (int, float)) else 0
            company.website_specializations = _clean_list(payload.get("specializations"))
            company.website_service_areas = _clean_list(payload.get("service_areas"))
            company.website_notable_projects = _clean_list(payload.get("notable"))
            session.commit()
            researched += 1
        except Exception as exc:
            session.rollback()
            print(
                f"[ArchCompanies] Website research failed for {_printable(company.name[:50])}: {exc}"
            )

    print(f"[ArchCompanies] Website research complete: {researched} firms")
    return researched
