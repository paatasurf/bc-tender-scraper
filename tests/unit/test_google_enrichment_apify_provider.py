"""Unit tests for Apify provider normalization."""

from __future__ import annotations

from pipeline.google_enrichment.apify_provider import normalize_apify_item


def test_normalize_apify_item_maps_core_fields():
    candidate = normalize_apify_item(
        {
            "placeId": "ChIJtest123",
            "title": "Pontem Group Ltd.",
            "totalScore": 4.6,
            "reviewsCount": 18,
            "categoryName": "General contractor",
            "address": "100 Main St, Vancouver, BC",
            "phone": "+1 604-555-0100",
            "website": "https://example.com",
            "url": "https://maps.google.com/?cid=1",
            "location": {"lat": 49.28, "lng": -123.12},
            "permanentlyClosed": False,
            "temporarilyClosed": False,
        }
    )
    assert candidate is not None
    assert candidate.place_id == "ChIJtest123"
    assert candidate.name == "Pontem Group Ltd."
    assert candidate.rating == 4.6
    assert candidate.review_count == 18
    assert candidate.business_status == "OPERATIONAL"
    assert candidate.lat == 49.28


def test_normalize_apify_item_rejects_missing_place_id():
    assert normalize_apify_item({"title": "No Place"}) is None
