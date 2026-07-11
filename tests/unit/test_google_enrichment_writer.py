"""Unit tests for CompanyGoogleWriter allowlist enforcement."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from db.models import Company
from pipeline.google_enrichment.constants import enriched
from pipeline.google_enrichment.models import GoogleEnrichmentResult, PlaceCandidate
from pipeline.google_enrichment.writer import (
    FORBIDDEN_COLUMNS,
    WRITABLE_GOOGLE_COLUMNS,
    CompanyGoogleWriter,
    build_google_update_payload,
    validate_update_keys,
)


def _sample_result(**overrides) -> GoogleEnrichmentResult:
    place = PlaceCandidate(
        place_id="ChIJtest123",
        name="Test Co",
        rating=4.5,
        review_count=12,
        category="General contractor",
        formatted_address="123 Main St, Vancouver, BC",
        phone="+1 604-555-0100",
        google_maps_url="https://maps.google.com/?cid=1",
        google_website="https://example.com",
        business_status="OPERATIONAL",
        lat=49.2827,
        lng=-123.1207,
    )
    defaults = {
        "company_id": 1,
        "place": place,
        "match_confidence": 0.91,
        "google_enrichment_status": enriched,
        "query_used": "Test Co Vancouver BC",
        "google_last_updated": datetime(2026, 7, 3, tzinfo=timezone.utc),
        "google_last_seen": datetime(2026, 7, 3, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return GoogleEnrichmentResult(**defaults)


def test_writable_columns_match_phase0_allowlist():
    expected = {
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
        "google_business_name",
        "google_enrichment_provider",
        "google_enrichment_status",
        "google_lat",
        "google_lng",
    }
    assert WRITABLE_GOOGLE_COLUMNS == expected
    assert CompanyGoogleWriter.allowlisted_columns() == expected


def test_build_google_update_payload_only_allowlisted_keys():
    payload = build_google_update_payload(_sample_result())
    assert set(payload.keys()) == WRITABLE_GOOGLE_COLUMNS
    assert payload["google_place_id"] == "ChIJtest123"
    assert payload["google_reviews_count"] == 12
    assert payload["google_lat"] == 49.2827
    assert payload["google_lng"] == -123.1207


def test_writer_apply_persists_lat_lng():
    company = Company(id=7, name="Geo Co")
    session = MagicMock()
    session.get.return_value = company
    CompanyGoogleWriter().apply(session, 7, _sample_result())
    assert company.google_lat == 49.2827
    assert company.google_lng == -123.1207


def test_validate_update_keys_rejects_forbidden_columns():
    with pytest.raises(ValueError, match="forbidden"):
        validate_update_keys({"name", "google_place_id"})
    with pytest.raises(ValueError, match="forbidden"):
        validate_update_keys({"lifecycle_status"})
    with pytest.raises(ValueError, match="forbidden"):
        validate_update_keys({"website"})
    with pytest.raises(ValueError, match="forbidden"):
        validate_update_keys({"ai_summary"})


def test_validate_update_keys_rejects_unknown_columns():
    with pytest.raises(ValueError, match="non-google"):
        validate_update_keys({"google_place_id", "created_at"})


def test_forbidden_columns_do_not_overlap_writable():
    overlap = WRITABLE_GOOGLE_COLUMNS & FORBIDDEN_COLUMNS
    assert not overlap


def test_writer_apply_updates_only_google_fields():
    company = Company(
        id=42,
        name="Original Name",
        website="https://curated.example",
        primary_address="999 Permit Ave",
        lifecycle_status="active",
        ai_summary="manual summary",
        total_projects=50,
    )
    session = MagicMock()
    session.get.return_value = company

    writer = CompanyGoogleWriter()
    writer.apply(session, 42, _sample_result())

    assert company.google_place_id == "ChIJtest123"
    assert company.google_rating == 4.5
    assert company.google_enrichment_status == enriched
    assert company.name == "Original Name"
    assert company.website == "https://curated.example"
    assert company.primary_address == "999 Permit Ave"
    assert company.lifecycle_status == "active"
    assert company.ai_summary == "manual summary"
    assert company.total_projects == 50


def test_writer_raises_when_company_missing():
    session = MagicMock()
    session.get.return_value = None
    writer = CompanyGoogleWriter()
    with pytest.raises(ValueError, match="Company not found"):
        writer.apply(session, 999, _sample_result())
