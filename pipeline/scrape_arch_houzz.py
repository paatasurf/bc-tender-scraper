from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchCompany
from pipeline.company_intelligence import _batch_limit

DEFAULT_HOUZZ_BATCH_LIMIT = 25
REQUEST_DELAY_SECONDS = 2.5  # polite crawling: 2-3s between requests
MAX_LIST_ITEMS = 15

# Houzz pro profile URLs look like
# https://www.houzz.com/professionals/<category>/<slug>-pfvwus-pf~<id>
PROFILE_URL_RE = re.compile(
    r"https://www\.houzz\.com/professionals/[a-z0-9-]+/([a-z0-9-]+)-pfvwus-pf~\d+"
)

# Houzz's keyword search is a client-side SPA that ignores the query string, so
# discovery walks the server-rendered local directory pages instead.
DIRECTORY_CATEGORIES = (
    "architects-and-building-designers",
    "interior-designers-and-decorators",
    "design-build-firms",
)
DIRECTORY_CITIES = ("Vancouver--BC", "Burnaby--BC", "Surrey--BC", "Victoria--BC")
DIRECTORY_PAGE_SIZE = 15
DIRECTORY_PAGES_PER_LIST = 4  # first 4 pages (~60 pros) per category+city
DIRECTORY_URL = "https://www.houzz.com/professionals/{category}/c/{city}/p/{offset}"

# Words too generic to identify a firm: legal suffixes plus industry vocabulary
# that appears in almost every architecture / design firm name.
NAME_STOP_WORDS = {
    "the", "and", "of", "ltd", "inc", "llp", "llc", "corp", "co", "company",
    "limited", "corporation", "firm", "group", "studio", "studios",
    "architecture", "architectural", "architects", "architect", "design",
    "designs", "designer", "designers", "interior", "interiors", "decorators",
    "planning", "associates", "partners", "collective", "drafting", "building",
    "workshop", "atelier", "office",
}

# Location words that Houzz sometimes appends to profile slugs.
SLUG_LOCATION_WORDS = {"vancouver", "burnaby", "surrey", "victoria", "bc", "north", "west"}

# Heuristic buckets for classifying Houzz project slugs into industry segments.
PROJECT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Residential": (
        "residence", "home", "house", "condo", "apartment", "loft", "bungalow",
        "townhouse", "penthouse", "cottage", "cabin", "kitchen", "bathroom",
        "bedroom", "basement", "laneway", "duplex", "villa",
    ),
    "Commercial": (
        "office", "retail", "store", "shop", "showroom", "workplace", "commercial",
        "clinic", "dental", "medical", "bank", "gym", "fitness",
    ),
    "Hospitality": (
        "hotel", "restaurant", "cafe", "bar", "lounge", "resort", "spa", "winery",
        "brewery", "hospitality",
    ),
    "Institutional": ("school", "university", "library", "church", "community", "civic"),
}


def _printable(text: str) -> str:
    """Console-safe name for logging (Windows consoles may not be UTF-8)."""
    return text.encode("ascii", "replace").decode()


def _normalized_tokens(name: str) -> list[str]:
    return [
        t
        for t in re.split(r"[^a-z0-9]+", name.lower())
        if len(t) > 1 and t not in NAME_STOP_WORDS
    ]


def _slug_matches_name(slug: str, name: str) -> bool:
    """Accept a profile only when the distinctive (non-generic) tokens of the
    firm name and the slug match exactly in both directions. Prevents generic
    words like "interior" or "architecture" from pairing unrelated firms."""
    name_tokens = set(_normalized_tokens(name))
    slug_tokens = {
        t
        for t in slug.split("-")
        if len(t) > 1 and t not in NAME_STOP_WORDS and t not in SLUG_LOCATION_WORDS
    }
    if not name_tokens or not slug_tokens:
        return False
    return name_tokens == slug_tokens


def _match_in_index(profile_index: dict[str, str], name: str) -> str:
    for slug, url in profile_index.items():
        if _slug_matches_name(slug, name):
            return url
    return ""


def _extract_local_business(html: str) -> dict[str, Any]:
    """Pull the LocalBusiness JSON-LD block from a Houzz profile page."""
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "LocalBusiness":
                return item
    return {}


def _extract_projects_count(html: str) -> int | None:
    match = re.search(r">(\d+)\s*Projects<", html)
    return int(match.group(1)) if match else None


def _extract_project_types(html: str) -> list[str]:
    """Classify the profile's project slugs into industry segments; fall back to
    the Houzz professional category when no projects are listed."""
    slugs = re.findall(r"projects/([a-z0-9-]+)-pj-vj", html)
    types: list[str] = []
    for segment, keywords in PROJECT_TYPE_KEYWORDS.items():
        if any(any(kw in slug for kw in keywords) for slug in set(slugs)):
            types.append(segment)
    if types:
        return types

    match = re.search(r'"proTypeDisplayName":"([^"]+)"', html)
    return [match.group(1)] if match else []


def _extract_service_areas(business: dict[str, Any]) -> list[str]:
    area = business.get("areaServed") or {}
    name = str(area.get("name") or "") if isinstance(area, dict) else ""
    return [part.strip() for part in name.split(",") if part.strip()][:MAX_LIST_ITEMS]


def _apply_profile(company: ArchCompany, url: str, html: str) -> None:
    business = _extract_local_business(html)
    rating = (business.get("aggregateRating") or {}) if business else {}

    company.houzz_profile_url = url[:500]
    company.houzz_projects_count = _extract_projects_count(html)
    company.houzz_project_types = _extract_project_types(html)[:MAX_LIST_ITEMS]
    company.houzz_service_areas = _extract_service_areas(business)

    try:
        value = rating.get("ratingValue")
        company.houzz_rating = float(value) if value is not None else None
    except (TypeError, ValueError):
        company.houzz_rating = None
    try:
        company.houzz_reviews_count = int(rating.get("reviewCount") or 0)
    except (TypeError, ValueError):
        company.houzz_reviews_count = 0


async def _build_profile_index(crawler: Any, config: Any) -> dict[str, str]:
    """Crawl the BC directory listings once and map profile slug -> profile URL."""
    index: dict[str, str] = {}
    for category in DIRECTORY_CATEGORIES:
        for city in DIRECTORY_CITIES:
            for page in range(DIRECTORY_PAGES_PER_LIST):
                url = DIRECTORY_URL.format(
                    category=category, city=city, offset=page * DIRECTORY_PAGE_SIZE
                )
                try:
                    result = await crawler.arun(url, config=config)
                except Exception as exc:
                    print(f"[ArchCompanies] Houzz directory page failed: {exc}")
                    continue
                html = result.html or "" if result.success else ""
                found = 0
                for match in PROFILE_URL_RE.finditer(html):
                    index.setdefault(match.group(1), match.group(0))
                    found += 1
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                if found == 0:
                    break  # past the last page of this listing
    print(f"[ArchCompanies] Houzz directory index: {len(index)} profiles")
    return index


async def _scrape_batch(session: Session, companies: list[ArchCompany]) -> int:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=45_000,
        verbose=False,
    )

    scraped = 0
    async with AsyncWebCrawler(config=browser_config) as crawler:
        profile_index = await _build_profile_index(crawler, run_config)

        for index, company in enumerate(companies, start=1):
            print(f"[ArchCompanies] Houzz {index}/{len(companies)}: {_printable(company.name[:70])}")
            try:
                url = company.houzz_profile_url or _match_in_index(profile_index, company.name)
                if url:
                    result = await crawler.arun(url, config=run_config)
                    if not result.success:
                        raise RuntimeError(result.error_message or "profile crawl failed")
                    _apply_profile(company, url, result.html or "")
                    scraped += 1
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)
                else:
                    # Mark as attempted so subsequent runs move on to other firms.
                    company.houzz_reviews_count = 0
                session.commit()
            except Exception as exc:
                session.rollback()
                print(f"[ArchCompanies] Houzz scrape failed for {_printable(company.name[:50])}: {exc}")

    return scraped


def scrape_arch_houzz(session: Session) -> int:
    """Find Houzz pro profiles for arch companies with Crawl4AI (directory-page
    discovery + profile parsing) and store projects, types, service areas,
    reviews, and rating into the houzz_* columns."""
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        print("[ArchCompanies] Skipping Houzz scrape: crawl4ai is not installed.")
        return 0

    limit = _batch_limit("ARCH_COMPANY_HOUZZ_MAX_PER_RUN", DEFAULT_HOUZZ_BATCH_LIMIT)
    companies = session.scalars(
        select(ArchCompany)
        .where(ArchCompany.houzz_reviews_count.is_(None), ArchCompany.name != "")
        .order_by(ArchCompany.total_value.desc())
        .limit(limit)
    ).all()

    print(f"[ArchCompanies] Houzz scrape: {len(companies)} firms queued (max {limit})")
    if not companies:
        return 0

    try:
        scraped = asyncio.run(_scrape_batch(session, companies))
    except Exception as exc:
        print(f"[ArchCompanies] Houzz scrape aborted: {exc}")
        return 0

    print(f"[ArchCompanies] Houzz scrape complete: {scraped} profiles found")
    return scraped
