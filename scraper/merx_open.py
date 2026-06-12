from __future__ import annotations

import requests

from scraper.config import (
    MERX_ARCH_BC_LOCATION,
    MERX_CONSTRUCTION_CATEGORY,
    MERX_OPEN_LIST_PATH,
    MERX_OPEN_SOURCE,
)
from scraper.merx_common import iter_merx_listing_pages
from scraper.models import Tender


def _listing_to_tender(listing: dict[str, str]) -> Tender:
    return Tender(
        title=listing["title"],
        organization=listing["organization"],
        category=MERX_CONSTRUCTION_CATEGORY,
        posted_date="",
        closing_date=listing["closing_date"],
        estimated_value="",
        location=listing["location"],
        tender_id=listing["tender_id"],
        url=listing["url"],
        source=MERX_OPEN_SOURCE,
    )


def scrape_merx_open_tenders(session: requests.Session) -> list[Tender]:
    params = {"location": MERX_ARCH_BC_LOCATION}
    listings = list(
        iter_merx_listing_pages(
            session,
            MERX_OPEN_LIST_PATH,
            params,
            log_prefix="[MERX Open]",
        )
    )
    print(f"[MERX Open] Found {len(listings)} British Columbia open opportunities")
    return [_listing_to_tender(listing) for listing in listings]
