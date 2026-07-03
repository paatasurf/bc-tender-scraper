"""Unit tests for Google enrichment query builder."""

from __future__ import annotations

from db.models import Company
from pipeline.google_enrichment.query_builder import build_refresh_query, build_search_query


def test_build_search_query_city_province():
    company = Company(
        id=1,
        name="Pontem Group",
        primary_city="Vancouver",
        primary_province="BC",
    )
    assert build_search_query(company) == "Pontem Group Vancouver BC"


def test_build_search_query_with_street():
    company = Company(
        id=1,
        name="Pontem Group",
        primary_city="Vancouver",
        primary_province="BC",
        primary_address="100 Main St, Vancouver, BC",
    )
    assert build_search_query(company) == "Pontem Group 100 Main St Vancouver BC"


def test_build_refresh_query_uses_place_id():
    company = Company(
        id=1,
        name="Pontem Group",
        google_place_id="ChIJabc123",
    )
    assert build_refresh_query(company) == "place_id:ChIJabc123"
