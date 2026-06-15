"""Unit tests for deterministic construction match scoring."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from db.models import CommercialTender, Company, Tender
from pipeline.scoring.construction_match_scoring import (
    CANONICAL_KEYS,
    score_construction_match,
    score_keywords,
    score_location,
)


def _company(**overrides) -> Company:
    defaults = {
        "id": 1,
        "name": "Pacific Build Co Ltd",
        "total_projects": 40,
        "avg_project_value": 500_000.0,
        "project_types": ["Building", "Alteration"],
        "neighborhoods": ["Vancouver"],
        "trade_tags": ["general contractor", "concrete"],
        "dominant_sector": "commercial",
        "primary_trade": "general_contractor",
        "ai_reliability_score": 80,
        "google_address": "123 Main Street, Vancouver, BC",
        "primary_city": "Vancouver",
        "primary_province": "BC",
    }
    defaults.update(overrides)
    return Company(**defaults)


def _federal_tender(**overrides) -> Tender:
    future = (date.today() + timedelta(days=20)).isoformat()
    defaults = {
        "id": 10,
        "title": "Building Renovation — Vancouver Civic Centre",
        "organization": "City of Vancouver",
        "category": "Construction",
        "closing_date": future,
        "estimated_value": "$750,000",
        "estimated_value_numeric": 750_000.0,
        "location": "Vancouver BC",
        "url": "https://example.com/tender/10",
    }
    defaults.update(overrides)
    return Tender(**defaults)


def _commercial_tender(**overrides) -> CommercialTender:
    future = (date.today() + timedelta(days=20)).isoformat()
    defaults = {
        "id": 20,
        "title": "Commercial Concrete Repair",
        "company": "Metro Vancouver",
        "category": "Commercial",
        "deadline": future,
        "value": "$600,000",
        "estimated_value_numeric": 600_000.0,
        "url": "https://example.com/commercial/20",
    }
    defaults.update(overrides)
    return CommercialTender(**defaults)


def test_score_construction_match_sum_invariant_federal():
    scored = score_construction_match(_company(), _federal_tender(), "federal")
    component_sum = sum(f.points for f in scored.breakdown)
    api_sum = sum(item["points"] for item in scored.api_breakdown.values())
    assert scored.score == component_sum
    assert scored.score == api_sum
    assert len(scored.breakdown_json) == len(CANONICAL_KEYS)


def test_score_construction_match_sum_invariant_commercial():
    scored = score_construction_match(_company(), _commercial_tender(), "commercial")
    api_sum = sum(item["points"] for item in scored.api_breakdown.values())
    assert scored.score == api_sum


def test_empty_project_history_still_scores():
    company = _company(project_types=[], trade_tags=[], dominant_sector="", primary_trade="")
    scored = score_construction_match(company, _federal_tender(), "federal")
    assert scored.score >= 0
    assert scored.score == sum(item["points"] for item in scored.api_breakdown.values())


def test_legacy_score_mismatch_scenario_deterministic_total():
    """Simulates old bug: high declared score vs lower component sum — engine uses sum only."""
    scored = score_construction_match(
        _company(neighborhoods=[], primary_city="", google_address=""),
        _federal_tender(title="Unrelated consulting study", category="Professional Services"),
        "federal",
    )
    assert scored.score == sum(item["points"] for item in scored.api_breakdown.values())
    assert scored.score <= 100


def test_expired_deadline_zero_freshness():
    scored = score_construction_match(
        _company(),
        _federal_tender(closing_date="2020-01-01"),
        "federal",
    )
    fresh = next(f for f in scored.breakdown if f.factor == "freshness")
    assert fresh.points == 0
    assert "closed" in fresh.detail.lower() or "ago" in fresh.detail.lower()


def test_location_does_not_use_street_address():
    company = _company(
        neighborhoods=[],
        primary_city="",
        primary_province="",
        geographic_reach="",
        google_address="123 Main Street, Victoria, BC",
    )
    factor = score_location(company, _federal_tender(location="Victoria BC"), "federal")
    assert factor.points == 0


def test_location_uses_neighborhood_tokens():
    company = _company(neighborhoods=["Vancouver"], primary_city="", primary_province="")
    factor = score_location(company, _federal_tender(location="Vancouver BC"), "federal")
    assert factor.points > 0


def test_api_breakdown_seven_keys():
    scored = score_construction_match(_company(), _federal_tender(), "federal")
    assert set(scored.api_breakdown.keys()) == {
        "keywords",
        "category",
        "specialization",
        "location",
        "value",
        "reliability",
        "freshness",
    }


def test_all_components_have_detail_strings():
    scored = score_construction_match(_company(), _federal_tender(), "federal")
    for factor in scored.breakdown:
        assert factor.detail.strip()


def test_keywords_match_construction_vocabulary():
    factor = score_keywords(
        _company(),
        _federal_tender(title="Building renovation and concrete repair Vancouver"),
        "federal",
    )
    assert factor.points > 0
