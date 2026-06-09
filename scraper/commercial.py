from __future__ import annotations

import requests

from scraper.bc_housing_commercial import scrape_bc_housing_commercial
from scraper.bidcentral_commercial import scrape_bidcentral_commercial
from scraper.civicinfo_commercial import scrape_civicinfo_commercial
from scraper.commercial_common import save_commercial_tenders
from scraper.config import COMMERCIAL_TENDERS_CSV
from scraper.models import CommercialTender


def scrape_commercial_tenders(session: requests.Session) -> list[CommercialTender]:
    tenders: list[CommercialTender] = []
    seen_urls: set[str] = set()

    for scrape_fn, label in (
        (scrape_bidcentral_commercial, "BidCentral"),
        (scrape_bc_housing_commercial, "BC Housing"),
        (scrape_civicinfo_commercial, "CivicInfo"),
    ):
        try:
            batch = scrape_fn(session)
        except Exception as exc:
            print(f"[Commercial] {label} failed: {exc}")
            continue

        for tender in batch:
            if tender.url in seen_urls:
                continue
            seen_urls.add(tender.url)
            tenders.append(tender)

    save_commercial_tenders(tenders)
    print(f"[Commercial] Saved {len(tenders)} tenders to {COMMERCIAL_TENDERS_CSV}")
    return tenders
