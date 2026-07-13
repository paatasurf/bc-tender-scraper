"""Unit tests for pipeline/scoring/rps.py — Revenue Pursuit Score."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.rps import (
    _award_signal_raw,
    _geo_score,
    _overlap_score,
    _size_score,
    _weight_table,
    score_active_tender,
)


def _make_profile(**overrides) -> CapabilityProfile:
    defaults = dict(
        version=1,
        computed_at="2024-01-01T00:00:00Z",
        company_id=1,
        kind="construction",
        name="Test Builders",
        company_type="General Contractor",
        primary_trade="general_building",
        trade_tags=["general_building", "concrete"],
        trade_confidence=0.8,
        project_types=["commercial", "residential"],
        project_type_distribution={"commercial": 0.6},
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
        text_blob="Road Construction Project City of Vancouver general building commercial",
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


class TestOverlapScore:
    def test_no_needles_returns_zero(self):
        score, detail = _overlap_score([], "some haystack text")
        assert score == 0
        assert detail == ""

    def test_no_match_returns_zero(self):
        score, detail = _overlap_score(
            ["plumbing", "hvac"], "road construction project"
        )
        assert score == 0
        assert detail == ""

    def test_single_match(self):
        score, detail = _overlap_score(["commercial"], "commercial building project")
        assert score == 55
        assert "commercial" in detail

    def test_multiple_matches_cap_at_100(self):
        needles = ["road", "construction", "building", "commercial", "project"]
        score, detail = _overlap_score(
            needles, "road construction building commercial project"
        )
        assert score == 100

    def test_case_insensitive(self):
        score, _ = _overlap_score(["COMMERCIAL"], "commercial building")
        assert score > 0

    def test_empty_needle_skipped(self):
        score, _ = _overlap_score(["", "commercial"], "commercial building")
        assert score == 55


class TestSizeScore:
    def test_no_baseline_returns_50(self):
        score, detail = _size_score(0, 500_000)
        assert score == 50
        assert "not stated" in detail.lower()

    def test_no_value_returns_50(self):
        score, detail = _size_score(500_000, 0)
        assert score == 50

    def test_aligned_range(self):
        score, detail = _size_score(500_000, 700_000)
        assert score == 95
        assert "aligned" in detail.lower()

    def test_broader_range(self):
        score, detail = _size_score(500_000, 1_500_000)
        assert score == 75
        assert "broader" in detail.lower()

    def test_stretch_range(self):
        score, detail = _size_score(500_000, 4_000_000)
        assert score == 45
        assert "stretch" in detail.lower()

    def test_mismatch_range(self):
        score, detail = _size_score(100_000, 5_000_000)
        assert score == 10
        assert "mismatch" in detail.lower()


class TestGeoScore:
    def test_service_city_match(self):
        profile = _make_profile(service_cities=["Vancouver", "Burnaby"])
        opp = _make_opp(geography_text="Burnaby BC", organization="Corp", title="Job")
        score, detail = _geo_score(profile, opp)
        assert score == 90
        assert "Burnaby" in detail

    def test_neighborhood_match(self):
        profile = _make_profile(
            service_cities=[], neighborhoods=["Kitsilano", "Downtown"]
        )
        opp = _make_opp(
            geography_text="Downtown area", organization="Corp", title="Job"
        )
        score, detail = _geo_score(profile, opp)
        assert score == 80
        assert "Downtown" in detail

    def test_bc_market_fallback(self):
        profile = _make_profile(service_cities=["Victoria"], neighborhoods=[])
        opp = _make_opp(
            geography_text="somewhere", organization="Corp", title="Vancouver project"
        )
        score, detail = _geo_score(profile, opp)
        assert score == 65
        assert "British Columbia" in detail

    def test_outside_core_area(self):
        profile = _make_profile(
            service_cities=["Vancouver"], neighborhoods=["Kitsilano"]
        )
        opp = _make_opp(
            geography_text="Calgary Alberta",
            organization="Alberta Corp",
            title="Alberta Job",
        )
        score, detail = _geo_score(profile, opp)
        assert score == 30
        assert "Outside" in detail


class TestAwardSignalRaw:
    def test_no_matches(self):
        profile = _make_profile(
            award_categories=["Plumbing"],
            award_clients=["City of Victoria"],
            market_segments=["provincial"],
        )
        opp = _make_opp(
            text_blob="electrical work in Kelowna", market_segment="federal"
        )
        score, detail = _award_signal_raw(profile, opp)
        assert score == 0
        assert detail == ""

    def test_category_match(self):
        profile = _make_profile(
            award_categories=["Construction"],
            award_clients=[],
            market_segments=[],
        )
        opp = _make_opp(text_blob="general construction project")
        score, detail = _award_signal_raw(profile, opp)
        assert score == 40
        assert "Category" in detail

    def test_client_match(self):
        profile = _make_profile(
            award_categories=[],
            award_clients=["City of Vancouver"],
            market_segments=[],
        )
        opp = _make_opp(
            text_blob="project for city of vancouver", organization="City of Vancouver"
        )
        score, detail = _award_signal_raw(profile, opp)
        assert score == 35
        assert "client" in detail.lower()

    def test_segment_match(self):
        profile = _make_profile(
            award_categories=[],
            award_clients=[],
            market_segments=["municipal"],
        )
        opp = _make_opp(market_segment="municipal")
        score, detail = _award_signal_raw(profile, opp)
        assert score == 25
        assert "Segment" in detail

    def test_combined_signals_capped(self):
        profile = _make_profile(
            award_categories=["Construction"],
            award_clients=["City of Vancouver"],
            market_segments=["municipal"],
        )
        opp = _make_opp(
            text_blob="construction city of vancouver municipal",
            organization="City of Vancouver",
            market_segment="municipal",
        )
        score, _ = _award_signal_raw(profile, opp)
        assert score == 100


class TestWeightTable:
    def test_architecture_weights(self):
        profile = _make_profile(kind="architecture")
        weights = _weight_table(profile)
        assert weights == (28, 22, 18, 12, 12, 0, 8)

    def test_trade_contractor_weights(self):
        profile = _make_profile(company_type="Trade Contractor")
        weights = _weight_table(profile)
        assert weights == (30, 15, 15, 15, 10, 12, 3)

    def test_general_contractor_weights(self):
        profile = _make_profile(company_type="General Contractor")
        weights = _weight_table(profile)
        assert weights == (25, 20, 18, 15, 10, 10, 2)


class TestScoreActiveTender:
    def test_returns_scored_recommendation(self):
        profile = _make_profile()
        opp = _make_opp()
        result = score_active_tender(profile, opp)

        assert 0 <= result.score <= 100
        assert result.score_label == "Revenue Pursuit Score"
        assert result.rank_key > 0
        assert len(result.breakdown) >= 7
        assert len(result.reasons) > 0

    def test_permit_signal_included(self):
        profile = _make_profile()
        opp = _make_opp()
        result_no_signal = score_active_tender(profile, opp, permit_signal=0)
        result_with_signal = score_active_tender(profile, opp, permit_signal=100)

        assert result_with_signal.score >= result_no_signal.score

    def test_award_confidence_boost_applied(self):
        profile = _make_profile(
            award_categories=["Construction"],
            award_clients=["City of Vancouver"],
            market_segments=["municipal"],
        )
        opp = _make_opp(
            text_blob="construction city of vancouver municipal",
            organization="City of Vancouver",
            market_segment="municipal",
        )
        result = score_active_tender(profile, opp)

        boost_factors = [
            b for b in result.breakdown if b.factor == "award_confidence_boost"
        ]
        assert len(boost_factors) == 1
        assert boost_factors[0].points in (4, 8)

    def test_score_capped_at_100(self):
        profile = _make_profile(
            award_categories=["Construction"],
            award_clients=["City of Vancouver"],
            market_segments=["municipal"],
            own_permit_count=50,
        )
        opp = _make_opp(
            text_blob="construction city of vancouver municipal general building commercial residential",
            organization="City of Vancouver",
            market_segment="municipal",
            trade_tags=["general_building", "concrete"],
            estimated_value=600_000,
        )
        result = score_active_tender(profile, opp, permit_signal=100)
        assert result.score <= 100
