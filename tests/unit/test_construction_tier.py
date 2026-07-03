"""Unit tests for deterministic Construction Tier Engine."""

from __future__ import annotations

from datetime import date

from db.company_canonical_constants import ENTITY_ROLE_CANONICAL
from db.construction_tier_constants import TIER_A, TIER_B, TIER_C, TIER_D, TIER_E
from db.models import Company
from pipeline.construction_geography import derive_geographic_presence
from pipeline.construction_tier import (
    EvidenceIndexes,
    PermitWindowStats,
    TenderStats,
    assign_tier_from_score,
    calculate_construction_score,
    compute_construction_tier,
    evaluate_enterprise_gate,
    gather_construction_evidence,
    parse_tier_filter,
)
from pipeline.construction_tier_config import (
    AWARD_WEIGHT,
    GEOGRAPHY_WEIGHT,
    LONGEVITY_WEIGHT,
    PERMIT_WEIGHT,
    TENDER_WEIGHT,
)


def _company(**kwargs) -> Company:
    defaults = {
        "name": "Test Co",
        "display_name": "Test Co",
        "entity_role": ENTITY_ROLE_CANONICAL,
        "total_projects": 0,
        "total_value": 0.0,
        "award_count": 0,
        "total_award_value": 0.0,
        "neighborhoods": [],
        "first_project_date": "",
        "last_project_date": "",
    }
    defaults.update(kwargs)
    return Company(**defaults)


def test_config_weights_sum_to_100():
    assert PERMIT_WEIGHT + AWARD_WEIGHT + TENDER_WEIGHT + GEOGRAPHY_WEIGHT + LONGEVITY_WEIGHT == 100


def test_parse_tier_filter_default_ab():
    assert parse_tier_filter("A,B") == {"tier_a", "tier_b"}


def test_parse_tier_filter_all():
    assert parse_tier_filter("ALL") is None


def test_tier_e_no_evidence():
    company = _company()
    result = compute_construction_tier(
        company,
        EvidenceIndexes(),
        reference_date=date(2026, 7, 1),
    )
    assert result.tier == TIER_E
    assert result.construction_score == 0


def test_tier_a_enterprise_contractor():
    company = _company(
        name="Pontem Group",
        total_projects=127,
        total_value=2_700_000_000.0,
        first_project_date="2019-08-13",
        last_project_date="2026-06-30",
        neighborhoods=["GRANVILLE STREET", "OAK STREET", "HEATHER STREET", "NANAIMO STREET"],
    )
    indexes = EvidenceIndexes(
        permits_24mo={8638: PermitWindowStats(count=20, value=500_000_000.0)},
        tender={8638: TenderStats(match_count=8, outcome_count=2, win_count=1)},
    )
    company.id = 8638
    result = compute_construction_tier(company, indexes, reference_date=date(2026, 7, 1))
    assert result.tier == TIER_A
    assert result.construction_score >= 45
    assert result.components["permit_activity"]["weight"] == PERMIT_WEIGHT


def test_score_is_integer_0_to_100():
    company = _company(
        total_projects=30,
        total_value=15_000_000.0,
        last_project_date="2026-01-15",
        neighborhoods=["A", "B", "C"],
    )
    company.id = 300
    indexes = EvidenceIndexes(
        permits_24mo={300: PermitWindowStats(count=6, value=2_000_000.0)},
    )
    evidence = gather_construction_evidence(company, indexes, reference_date=date(2026, 7, 1))
    breakdown = calculate_construction_score(evidence)
    assert isinstance(breakdown.construction_score, int)
    assert 0 <= breakdown.construction_score <= 100


def test_tier_derived_from_score_stages():
    company = _company(
        total_projects=15,
        total_value=3_000_000.0,
        first_project_date="2022-01-01",
        last_project_date="2025-12-01",
        neighborhoods=["MAIN STREET"],
    )
    company.id = 100
    indexes = EvidenceIndexes(permits_24mo={100: PermitWindowStats(count=4, value=800_000.0)})
    evidence = gather_construction_evidence(company, indexes, reference_date=date(2026, 7, 1))
    score = calculate_construction_score(evidence)
    gate = evaluate_enterprise_gate(evidence)
    tier = assign_tier_from_score(score.construction_score, evidence, enterprise_gate=gate)
    assert tier in {TIER_B, TIER_C}


def test_breakdown_sums_to_construction_score():
    company = _company(
        total_projects=30,
        total_value=15_000_000.0,
        award_count=1,
        total_award_value=1_000_000.0,
        last_project_date="2026-01-15",
        neighborhoods=["A", "B", "C"],
    )
    company.id = 300
    indexes = EvidenceIndexes(
        permits_24mo={300: PermitWindowStats(count=6, value=2_000_000.0)},
        tender={300: TenderStats(match_count=3, outcome_count=1, win_count=0)},
    )
    result = compute_construction_tier(company, indexes, reference_date=date(2026, 7, 1))
    component_sum = sum(part["score"] for part in result.components.values())
    assert abs(component_sum - result.construction_score) <= 1


def test_geographic_presence_abstracted_from_neighborhoods():
    company = _company(neighborhoods=["Vancouver", "Burnaby", "Vancouver"])
    presence = derive_geographic_presence(company)
    assert presence.location_count == 2
    assert presence.source == "neighborhoods"
    assert set(presence.locations) == {"Burnaby", "Vancouver"}


def test_geographic_presence_used_in_scoring_not_neighborhoods_directly():
    company = _company(
        total_projects=10,
        total_value=1_000_000.0,
        last_project_date="2026-01-01",
        neighborhoods=["A", "B", "C", "D", "E", "F"],
    )
    company.id = 400
    evidence = gather_construction_evidence(company, EvidenceIndexes(), reference_date=date(2026, 7, 1))
    assert evidence.geographic_presence.location_count == 6
    geo = evidence.geographic_presence
    breakdown = calculate_construction_score(evidence)
    geo_component = breakdown.components["geographic_presence"]
    assert geo_component["location_count"] == geo.location_count
    assert geo_component["source"] == "neighborhoods"
    assert geo_component["score"] == 2.0  # 6 locations / 3, capped at GEOGRAPHY_WEIGHT
