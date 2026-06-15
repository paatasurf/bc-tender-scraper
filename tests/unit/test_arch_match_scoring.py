"""Unit tests for deterministic architecture match scoring."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from db.models import ArchCompany, ArchTender
from pipeline.scoring.arch_match_scoring import (
    CANONICAL_KEYS,
    score_architecture_match,
    score_freshness,
    score_project_type,
    score_region,
    to_api_breakdown,
)


def _company(**overrides) -> ArchCompany:
    defaults = {
        "id": 1,
        "name": "Test Architects Inc",
        "total_projects": 25,
        "avg_project_value": 1_000_000.0,
        "project_types": ["Residential", "Commercial"],
        "neighborhoods": ["Vancouver"],
        "website_specializations": ["Residential Architecture"],
        "dominant_sector": "residential",
        "houzz_service_areas": ["Vancouver"],
        "value_p25": 500_000.0,
        "value_p75": 2_000_000.0,
    }
    defaults.update(overrides)
    return ArchCompany(**defaults)


def _tender(**overrides) -> ArchTender:
    future = (date.today() + timedelta(days=30)).isoformat()
    defaults = {
        "id": 10,
        "title": "Residential Design Services — Vancouver",
        "company": "City of Vancouver",
        "value": "$1,500,000",
        "deadline": future,
        "status": "Open",
        "category": "Residential Architecture",
        "url": "https://example.com/tender/10",
        "tender_id": "T-10",
    }
    defaults.update(overrides)
    return ArchTender(**defaults)


def test_score_architecture_match_sum_invariant():
    scored = score_architecture_match(_company(), _tender())
    component_sum = sum(f.points for f in scored.breakdown)
    api_sum = sum(item["points"] for item in scored.api_breakdown.values())
    assert scored.score == component_sum
    assert scored.score == api_sum
    assert len(scored.breakdown_json) == len(CANONICAL_KEYS)


def test_empty_project_history_zero_project_type():
    company = _company(total_projects=0, project_types=[], houzz_project_types=[])
    factor = score_project_type(company, _tender())
    assert factor.points == 0


def test_expired_deadline_zero_freshness():
    factor = score_freshness(_tender(deadline="2020-01-01"))
    assert factor.points == 0
    assert "expired" in factor.detail.lower()


def test_missing_value_zero_value_fit():
    scored = score_architecture_match(
        _company(avg_project_value=0, value_p25=None, value_p75=None),
        _tender(value=""),
    )
    value_factor = next(f for f in scored.breakdown if f.factor == "value_fit")
    assert value_factor.points == 0


def test_no_region_overlap():
    company = _company(neighborhoods=["Kelowna"], houzz_service_areas=[], website_service_areas=[])
    factor = score_region(company, _tender(company="City of Vancouver", title="Vancouver project"))
    assert factor.points in (0, 8, 15)


def test_region_does_not_use_street_address():
    company = _company(
        google_address="123 Main Street, Vancouver, BC",
        lat=49.28,
        lng=-123.12,
        neighborhoods=[],
        houzz_service_areas=[],
        website_service_areas=[],
    )
    factor = score_region(company, _tender(company="Victoria BC", title="Capital project"))
    assert factor.points == 0


def test_api_breakdown_maps_seven_keys():
    scored = score_architecture_match(_company(), _tender())
    keys = set(scored.api_breakdown.keys())
    assert keys == {"keywords", "category", "specialization", "location", "value", "reliability", "freshness"}
    assert scored.api_breakdown["keywords"]["points"] == 0
    assert scored.api_breakdown["reliability"]["points"] == 0


def test_strong_residential_match_nonzero_category():
    scored = score_architecture_match(_company(total_projects=50), _tender())
    category_pts = scored.api_breakdown["category"]["points"]
    assert category_pts > 0
    assert scored.score >= category_pts
