"""CompanyGoogleWriter — allowlisted Google field updates only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from db.models import Company
from pipeline.google_enrichment.models import GoogleEnrichmentResult

# Phase 0 allowlist — must match architecture non-goals enforcement.
WRITABLE_GOOGLE_COLUMNS: frozenset[str] = frozenset(
    {
        "google_place_id",
        "google_rating",
        "google_reviews_count",
        "google_address",
        "google_phone",
        "google_business_category",
        "google_maps_url",
        "google_business_status",
        "google_last_updated",
        "google_last_seen",
        "google_match_confidence",
        "google_query_used",
        "google_website",
        "google_enrichment_status",
        "google_lat",
        "google_lng",
    }
)

# Columns that must never be modified by this writer (regression guard).
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "canonical_vendor_name",
        "website",
        "primary_address",
        "primary_city",
        "primary_province",
        "lifecycle_status",
        "lifecycle_status_override",
        "is_operating",
        "last_activity_at",
        "status_changed_at",
        "ai_summary",
        "ai_reliability_score",
        "confidence_score",
        "company_type",
        "primary_trade",
        "capability_profile_json",
        "cip_json",
        "enrichment_status",
        "last_enriched_at",
        "total_projects",
        "total_value",
        "award_count",
    }
)


def build_google_update_payload(result: GoogleEnrichmentResult) -> dict[str, Any]:
    """Build the allowlisted update dict from an enrichment result."""
    place = result.place
    now = result.google_last_updated or datetime.now(timezone.utc)
    seen = result.google_last_seen or now
    payload: dict[str, Any] = {
        "google_place_id": place.place_id,
        "google_rating": place.rating,
        "google_reviews_count": place.review_count,
        "google_address": (place.formatted_address or "")[:500],
        "google_phone": (place.phone or "")[:50],
        "google_business_category": (place.category or "")[:200],
        "google_maps_url": (place.google_maps_url or "")[:500],
        "google_business_status": (place.business_status or "")[:50],
        "google_website": (place.google_website or "")[:500],
        "google_match_confidence": result.match_confidence,
        "google_query_used": (result.query_used or "")[:500],
        "google_enrichment_status": result.google_enrichment_status,
        "google_last_updated": now,
        "google_last_seen": seen,
        "google_lat": place.lat,
        "google_lng": place.lng,
    }
    unknown = set(payload) - WRITABLE_GOOGLE_COLUMNS
    if unknown:
        raise ValueError(f"Update payload contains non-allowlisted keys: {sorted(unknown)}")
    return payload


def validate_update_keys(keys: set[str]) -> None:
    """Raise if any key is outside the writable allowlist."""
    forbidden = keys & FORBIDDEN_COLUMNS
    if forbidden:
        raise ValueError(f"Attempted to modify forbidden columns: {sorted(forbidden)}")
    disallowed = keys - WRITABLE_GOOGLE_COLUMNS
    if disallowed:
        raise ValueError(f"Attempted to modify non-google columns: {sorted(disallowed)}")


class CompanyGoogleWriter:
    """Writes Google Business metadata to companies — allowlisted columns only."""

    def apply(self, session: Session, company_id: int, result: GoogleEnrichmentResult) -> Company:
        company = session.get(Company, company_id)
        if company is None:
            raise ValueError(f"Company not found: {company_id}")

        payload = build_google_update_payload(result)
        validate_update_keys(set(payload.keys()))

        for column, value in payload.items():
            setattr(company, column, value)

        return company

    @staticmethod
    def allowlisted_columns() -> frozenset[str]:
        return WRITABLE_GOOGLE_COLUMNS
