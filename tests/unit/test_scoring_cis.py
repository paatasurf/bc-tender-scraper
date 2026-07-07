"""Unit tests for pipeline/scoring/cis.py — Competitive Intelligence Score."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.cis import score_contract_award


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
        project_types=["commercial"],
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
        category="intelligence",
        subtype="contract_award",
        source_table="contract_awards",
        source_id=1,
        title="Road Work Award",
        organization="City of Vancouver",
        text_blob="road work construction city of vancouver",
        trade_tags=["general_building"],
        project_type_tags=["commercial"],
        market_segment="municipal",
        estimated_value=500_000.0,
        geography_text="Vancouver BC",
        deadline="2024-01-15",
        is_open=True,
        payload={},
        context="own_history",
    )
    defaults.update(overrides)
    return NormalizedOpportunity(**defaults)


class TestScoreContractAward:
    def test_returns_scored_recommendation(self):
        profile = _make_profile()
        opp = _make_opp()
        result = score_contract_award(profile, opp)

        assert 0 <= result.score <= 92
        assert result.score_label == "Competitive Intelligence Score"
        assert result.rank_key == float(result.score)
        assert len(result.breakdown) == 6
        assert len(result.reasons) > 0

    def test_high_score_with_matching_categories_and_clients(self):
        profile = _make_profile(
            award_categories=["Construction"],
            award_clients=["City of Vancouver"],
        )
        opp = _make_opp(
            text_blob="construction city of vancouver road building",
            context="own_history",
        )
        result = score_contract_award(profile, opp)

        assert result.score >= 60

    def test_low_score_with_no_matches(self):
        profile = _make_profile(
            award_categories=["Plumbing"],
            award_clients=["Unknown Corp"],
        )
        opp = _make_opp(
            text_blob="electrical work in kelowna",
            context="peer_award",
        )
        result = score_contract_award(profile, opp)

        assert result.score < 60

    def test_score_capped_at_92(self):
        profile = _make_profile(
            award_categories=["Construction"],
            award_clients=["City of Vancouver"],
        )
        opp = _make_opp(
            text_blob="construction city of vancouver",
            context="own_history",
        )
        result = score_contract_award(profile, opp)
        assert result.score <= 92

    def test_own_history_context_boosts_capability(self):
        profile = _make_profile(award_categories=[], award_clients=[])
        opp_own = _make_opp(context="own_history", text_blob="something unrelated")
        opp_peer = _make_opp(context="peer_award", text_blob="something unrelated")

        result_own = score_contract_award(profile, opp_own)
        result_peer = score_contract_award(profile, opp_peer)

        assert result_own.score >= result_peer.score

    def test_breakdown_factor_names(self):
        profile = _make_profile()
        opp = _make_opp()
        result = score_contract_award(profile, opp)

        factor_names = [b.factor for b in result.breakdown]
        assert "capability_fit" in factor_names
        assert "project_type_fit" in factor_names
        assert "similar_projects" in factor_names
        assert "budget_fit" in factor_names
        assert "geography" in factor_names
        assert "contract_award_signal" in factor_names
