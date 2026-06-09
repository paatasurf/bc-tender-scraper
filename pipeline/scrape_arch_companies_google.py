from __future__ import annotations

import time
from typing import Any, Iterator

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import ArchCompany
from pipeline.company_intelligence import (
    DEFAULT_GOOGLE_BATCH_LIMIT,
    GOOGLE_PLACES_SEARCH_URL,
    REQUEST_DELAY_SECONDS,
    _batch_limit,
    _google_api_key,
)

SEARCH_QUERIES = (
    "architecture firm Vancouver BC",
    "architectural studio Vancouver BC",
    "architecture company Surrey BC",
    "architecture firm Burnaby BC",
    "architecture firm Victoria BC",
    "architectural firm Kelowna BC",
)

PAGE_SIZE = 20
MAX_PAGES_PER_QUERY = 3  # Text Search returns at most 60 results per query.

SEARCH_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.location",
        "nextPageToken",
    )
)


def _search_places(api_key: str, query: str) -> Iterator[dict[str, Any]]:
    """Yield Google Places Text Search results for a query, following pagination."""
    page_token = ""
    for _ in range(MAX_PAGES_PER_QUERY):
        body: dict[str, Any] = {"textQuery": query, "pageSize": PAGE_SIZE}
        if page_token:
            body["pageToken"] = page_token

        response = requests.post(
            GOOGLE_PLACES_SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": SEARCH_FIELD_MASK,
            },
            json=body,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

        yield from payload.get("places", [])

        page_token = payload.get("nextPageToken", "")
        if not page_token:
            break
        time.sleep(REQUEST_DELAY_SECONDS)


def _upsert_place(session: Session, place: dict[str, Any]) -> str:
    """Insert or update one arch_companies row from a Places result.
    Returns "inserted", "updated", or "skipped"."""
    place_id = str(place.get("id") or "").strip()
    name = str((place.get("displayName") or {}).get("text") or "").strip()
    if not place_id or not name:
        return "skipped"

    company = session.scalars(
        select(ArchCompany).where(ArchCompany.google_place_id == place_id)
    ).first()
    if company is None:
        # A row may already exist from the permits aggregation under the same
        # name (which is unique), so claim it instead of inserting a duplicate.
        company = session.scalars(
            select(ArchCompany).where(func.lower(ArchCompany.name) == name.lower())
        ).first()

    inserted = company is None
    if inserted:
        company = ArchCompany(name=name[:300])
        session.add(company)

    location = place.get("location") or {}
    company.google_place_id = place_id
    company.google_rating = place.get("rating")
    company.google_reviews_count = int(place.get("userRatingCount") or 0)
    company.google_address = str(place.get("formattedAddress") or "")[:500]
    company.google_phone = str(place.get("nationalPhoneNumber") or "")[:50]
    company.website = str(place.get("websiteUri") or "")[:500]
    company.lat = location.get("latitude")
    company.lng = location.get("longitude")

    session.commit()
    return "inserted" if inserted else "updated"


def scrape_arch_companies_google(session: Session) -> int:
    """Discover BC architecture firms via Google Places Text Search and upsert
    them into arch_companies, keyed on google_place_id."""
    api_key = _google_api_key()
    if not api_key:
        print("[ArchCompanies] Skipping Google Places scrape: GOOGLE_PLACES_API_KEY is not set.")
        return 0

    limit = _batch_limit("ARCH_COMPANY_GOOGLE_MAX_PER_RUN", DEFAULT_GOOGLE_BATCH_LIMIT)
    print(f"[ArchCompanies] Scraping Google Places ({len(SEARCH_QUERIES)} queries, max {limit} firms)...")

    seen_place_ids: set[str] = set()
    inserted = 0
    updated = 0

    for query in SEARCH_QUERIES:
        if len(seen_place_ids) >= limit:
            break
        print(f"[ArchCompanies] Query: {query}")
        try:
            for place in _search_places(api_key, query):
                place_id = str(place.get("id") or "")
                if not place_id or place_id in seen_place_ids:
                    continue
                if len(seen_place_ids) >= limit:
                    break
                seen_place_ids.add(place_id)

                try:
                    outcome = _upsert_place(session, place)
                except Exception as exc:
                    session.rollback()
                    print(f"[ArchCompanies] Upsert failed: {exc}")
                    continue

                if outcome == "inserted":
                    inserted += 1
                elif outcome == "updated":
                    updated += 1
        except Exception as exc:
            print(f"[ArchCompanies] Query failed: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    total = inserted + updated
    print(
        f"[ArchCompanies] Google Places scrape complete: "
        f"{total} firms ({inserted} new, {updated} updated)"
    )
    return total
