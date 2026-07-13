"""Unit tests for pipeline/scoring/filters.py gate functions."""

from __future__ import annotations

from unittest.mock import patch

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.filters import (
    passes_capability_gate,
    passes_geography_gate,
    passes_value_gate,
)


def _make_profile(**overrides) -> CapabilityProfile:
    defaults = dict(
        version=1,
        computed_at="2024-01-01T00:00:00Z",
        company_id=1,
        kind="construction",
        name="Test Co",
        company_type="General Contractor",
        primary_trade="general_building",
        trade_tags=["general_building", "concrete"],
        trade_confidence=0.8,
        project_types=["commercial", "residential"],
        project_type_distribution={"commercial": 0.6, "residential": 0.4},
        neighborhoods=["Downtown", "Kitsilano"],
        service_cities=["Vancouver", "Burnaby"],
        avg_project_value=500_000.0,
        avg_award_value=600_000.0,
        award_count=10,
        award_categories=["Construction"],
        award_clients=["City of Vancouver"],
        buyer_levels=["municipal"],
        market_segments=["municipal"],
        own_permit_count=5,
    )
    defaults.update(overrides)
    return CapabilityProfile(**defaults)


def _make_opp(**overrides) -> NormalizedOpportunity:
    defaults = dict(
        category="active",
        subtype="federal_tender",
        source_table="tenders",
        source_id=1,
        title="Road Construction Project",
        organization="City of Vancouver",
        text_blob="Road Construction Project City of Vancouver general building",
        trade_tags=["general_building", "civil"],
        project_type_tags=["commercial"],
        market_segment="municipal",
        estimated_value=500_000.0,
        geography_text="Vancouver BC",
        deadline="2025-06-01",
        is_open=True,
        payload={"type": "commercial"},
    )
    defaults.update(overrides)
    return NormalizedOpportunity(**defaults)


class TestPassesCapabilityGate:
    def test_building_permit_always_passes(self):
        profile = _make_profile(primary_trade="roofing", trade_tags=["roofing"])
        opp = _make_opp(
            category="pipeline",
            subtype="building_permit",
            trade_tags=["electrical"],
        )
        assert passes_capability_gate(profile, opp) is True

    def test_high_match_score_passes(self):
        profile = _make_profile(
            primary_trade="general_building",
            trade_tags=["general_building", "concrete"],
        )
        opp = _make_opp(trade_tags=["general_building", "concrete"])
        assert passes_capability_gate(profile, opp) is True

    def test_low_match_score_fails(self):
        profile = _make_profile(primary_trade="landscaping", trade_tags=["landscaping"])
        opp = _make_opp(trade_tags=["electrical", "hvac"])
        # landscaping vs electrical/hvac returns 15 which equals the default min_match
        # so use a higher min_match to test the gate actually rejects
        assert passes_capability_gate(profile, opp, min_match=20) is False

    def test_custom_min_match_threshold(self):
        # general_building in opp_tags with matching primary -> score = 100
        # so min_match=101 should fail
        profile = _make_profile(
            primary_trade="general_building", trade_tags=["general_building"]
        )
        opp = _make_opp(trade_tags=["general_building"])
        assert passes_capability_gate(profile, opp, min_match=101) is False
        # but at min_match=100 it should pass
        assert passes_capability_gate(profile, opp, min_match=100) is True


class TestPassesValueGate:
    def test_no_value_always_passes(self):
        profile = _make_profile(avg_award_value=500_000)
        opp = _make_opp(estimated_value=0)
        assert passes_value_gate(profile, opp) is True

    def test_no_baseline_always_passes(self):
        profile = _make_profile(avg_award_value=0, avg_project_value=0)
        opp = _make_opp(estimated_value=100_000)
        assert passes_value_gate(profile, opp) is True

    def test_value_within_range_passes(self):
        profile = _make_profile(avg_award_value=500_000)
        opp = _make_opp(estimated_value=1_000_000)
        assert passes_value_gate(profile, opp) is True

    def test_value_too_high_fails(self):
        profile = _make_profile(avg_award_value=100_000)
        opp = _make_opp(estimated_value=5_000_000)
        assert passes_value_gate(profile, opp) is False

    def test_value_too_low_fails(self):
        profile = _make_profile(avg_award_value=1_000_000)
        opp = _make_opp(estimated_value=1_000)
        assert passes_value_gate(profile, opp) is False

    def test_uses_avg_project_value_as_fallback(self):
        profile = _make_profile(avg_award_value=0, avg_project_value=500_000)
        opp = _make_opp(estimated_value=600_000)
        assert passes_value_gate(profile, opp) is True


class TestPassesGeographyGate:
    def test_no_cities_or_neighborhoods_always_passes(self):
        profile = _make_profile(service_cities=[], neighborhoods=[])
        opp = _make_opp(geography_text="Unknown Location")
        assert passes_geography_gate(profile, opp) is True

    def test_bc_mention_passes(self):
        profile = _make_profile(service_cities=["Vancouver"])
        opp = _make_opp(
            title="Project in British Columbia",
            geography_text="Northern BC",
            organization="Provincial Gov",
        )
        assert passes_geography_gate(profile, opp) is True

    def test_vancouver_mention_passes(self):
        profile = _make_profile(service_cities=["Burnaby"])
        opp = _make_opp(
            title="Downtown Project",
            geography_text="Vancouver",
            organization="Corp",
        )
        assert passes_geography_gate(profile, opp) is True

    def test_service_city_match_passes(self):
        profile = _make_profile(service_cities=["Surrey"])
        opp = _make_opp(
            title="Road Work",
            geography_text="Surrey BC",
            organization="City of Surrey",
        )
        assert passes_geography_gate(profile, opp) is True

    def test_neighborhood_match_passes(self):
        profile = _make_profile(service_cities=[], neighborhoods=["Kitsilano"])
        opp = _make_opp(
            title="Renovation",
            geography_text="Kitsilano area",
            organization="Private",
        )
        assert passes_geography_gate(profile, opp) is True

    def test_architecture_kind_always_passes(self):
        profile = _make_profile(
            kind="architecture",
            service_cities=["Vancouver"],
            neighborhoods=["Downtown"],
        )
        opp = _make_opp(
            title="Project in Calgary",
            geography_text="Alberta",
            organization="Corp",
        )
        assert passes_geography_gate(profile, opp) is True

    def test_general_building_trade_passes(self):
        profile = _make_profile(
            primary_trade="general_building",
            service_cities=["Vancouver"],
            neighborhoods=[],
        )
        opp = _make_opp(
            title="Project Somewhere",
            geography_text="Unknown",
            organization="Corp",
        )
        assert passes_geography_gate(profile, opp) is True

    def test_unmatched_location_fails(self):
        profile = _make_profile(
            kind="construction",
            primary_trade="electrical",
            service_cities=["Vancouver"],
            neighborhoods=["Downtown"],
        )
        opp = _make_opp(
            title="Project in Calgary",
            geography_text="Alberta",
            organization="Alberta Corp",
        )
        assert passes_geography_gate(profile, opp) is False
