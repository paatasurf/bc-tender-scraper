"""Unit tests for pipeline/scoring/mps.py — Market Pipeline Score."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.mps import score_pipeline_permit


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
        neighborhoods=[],
        service_cities=["Vancouver"],
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
        category="pipeline",
        subtype="building_permit",
        source_table="permits",
        source_id=1,
        title="123 Main St",
        organization="Builder Corp",
        text_blob="commercial building new construction residential",
        trade_tags=["general_building"],
        project_type_tags=["commercial"],
        market_segment="municipal",
        estimated_value=300_000.0,
        geography_text="Vancouver BC",
        deadline="2024-03-01",
        is_open=True,
        payload={"type": "Commercial"},
        context="market_permit",
    )
    defaults.update(overrides)
    return NormalizedOpportunity(**defaults)


class TestScorePipelinePermit:
    def test_returns_scored_recommendation(self):
        profile = _make_profile()
        opp = _make_opp()
        result = score_pipeline_permit(profile, opp)

        assert 0 <= result.score <= 75
        assert result.score_label == "Market Pipeline Score"
        assert result.rank_key == float(result.score)
        assert len(result.breakdown) == 6
        assert len(result.reasons) > 0

    def test_score_capped_at_75(self):
        profile = _make_profile(
            trade_tags=["general_building", "concrete", "structural"],
            project_types=["commercial", "residential"],
        )
        opp = _make_opp(
            text_blob="commercial residential general building concrete",
            trade_tags=["general_building", "concrete", "structural"],
            context="own_permit",
        )
        result = score_pipeline_permit(profile, opp)
        assert result.score <= 75

    def test_own_permit_context_higher_score(self):
        profile = _make_profile()
        opp_own = _make_opp(context="own_permit")
        opp_market = _make_opp(context="market_permit")

        result_own = score_pipeline_permit(profile, opp_own)
        result_market = score_pipeline_permit(profile, opp_market)

        assert result_own.score >= result_market.score

    def test_project_type_match_boosts_score(self):
        profile = _make_profile(project_types=["commercial"])
        opp_match = _make_opp(text_blob="commercial building project")
        opp_no_match = _make_opp(text_blob="something entirely different here")

        result_match = score_pipeline_permit(profile, opp_match)
        result_no_match = score_pipeline_permit(profile, opp_no_match)

        assert result_match.score >= result_no_match.score

    def test_breakdown_factor_names(self):
        profile = _make_profile()
        opp = _make_opp()
        result = score_pipeline_permit(profile, opp)

        factor_names = [b.factor for b in result.breakdown]
        assert "capability_fit" in factor_names
        assert "project_type_fit" in factor_names
        assert "similar_projects" in factor_names
        assert "budget_fit" in factor_names
        assert "geography" in factor_names
        assert "permit_signal" in factor_names

    def test_geography_text_included_in_breakdown(self):
        profile = _make_profile()
        opp = _make_opp(geography_text="Burnaby BC")
        result = score_pipeline_permit(profile, opp)

        geo_factor = next(b for b in result.breakdown if b.factor == "geography")
        assert "Burnaby BC" in geo_factor.detail

    def test_payload_type_in_project_type_detail(self):
        profile = _make_profile()
        opp = _make_opp(payload={"type": "Residential"})
        result = score_pipeline_permit(profile, opp)

        ptype_factor = next(
            b for b in result.breakdown if b.factor == "project_type_fit"
        )
        assert "Residential" in ptype_factor.detail
