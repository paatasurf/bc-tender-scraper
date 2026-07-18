"""Unit tests for pipeline.scoring.relationship_growth (PR-E2).

Golden values below were captured by executing the real, unedited
score_relationship()/score_growth_tender() against fixed representative
inputs -- they pin the current weights/caps/breakdown structure so an
unintentional formula change fails these tests. Read-only: this file
imports the public scoring functions for testing but does not modify
pipeline/scoring/relationship_growth.py's production logic.
"""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.relationship_growth import score_growth_tender, score_relationship


def _make_profile(**overrides) -> CapabilityProfile:
    defaults = dict(
        version=1,
        computed_at="2026-01-01T00:00:00+00:00",
        company_id=1,
        kind="construction",
        name="Test Co",
        company_type="General Contractor",
        primary_trade="concrete",
        trade_tags=["concrete"],
        trade_confidence=0.8,
        project_types=["building"],
        project_type_distribution={},
        neighborhoods=[],
        service_cities=["Vancouver"],
        avg_project_value=500_000.0,
        avg_award_value=500_000.0,
        award_count=5,
        award_categories=["Construction"],
        award_clients=["City of Vancouver"],
        buyer_levels=["municipal"],
        market_segments=["public"],
        own_permit_count=10,
    )
    defaults.update(overrides)
    return CapabilityProfile(**defaults)


def _make_opp(**overrides) -> NormalizedOpportunity:
    defaults = dict(
        category="growth",
        subtype="tender",
        source_table="tenders",
        source_id=1,
        title="Structural upgrade project",
        organization="City of Vancouver",
        text_blob="structural upgrade project",
        trade_tags=["structural"],
        project_type_tags=["building"],
        market_segment="public",
        estimated_value=500_000.0,
        geography_text="Vancouver, BC",
        deadline="2026-12-31",
        is_open=True,
        payload={},
    )
    defaults.update(overrides)
    return NormalizedOpportunity(**defaults)


# ===================================================================
# score_relationship()
# ===================================================================


def test_relationship_golden_score_project_count_zero():
    """project_count=0, related_tender_count=0 (default) -- baseline
    golden value. Pins capability_fit's constant raw=70 contribution
    (21 pts) plus similar_projects at its floor (40 -> 16 pts) and zero
    contract_award_signal contribution."""
    profile = _make_profile()
    result = score_relationship(
        profile, entity_type="architect", entity_name="Acme Architects", project_count=0
    )
    assert result.score == 37
    breakdown_points = {b.factor: b.points for b in result.breakdown}
    assert breakdown_points == {
        "capability_fit": 21,
        "similar_projects": 16,
        "contract_award_signal": 0,
    }


def test_relationship_golden_scores_representative_inputs():
    """Multiple representative fixtures with exact expected scores --
    regression guard against unintentional weight/formula changes."""
    profile = _make_profile()
    cases = {
        1: 40,
        5: 53,
        8: 61,
    }
    for project_count, expected_score in cases.items():
        result = score_relationship(
            profile,
            entity_type="architect",
            entity_name="Acme Architects",
            project_count=project_count,
        )
        assert result.score == expected_score, project_count


def test_relationship_score_increases_with_project_count():
    profile = _make_profile()
    scores = [
        score_relationship(
            profile, entity_type="architect", entity_name="Acme", project_count=pc
        ).score
        for pc in (0, 1, 5, 8)
    ]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


def test_relationship_rel_raw_capped_at_100():
    """project_count=8 already saturates rel_raw at 100 (40 + 8*8 = 104,
    capped); project_count=20 must produce an identical score, proving
    the cap -- not just a coincidentally equal value from the formula."""
    profile = _make_profile()
    at_cap = score_relationship(
        profile, entity_type="architect", entity_name="Acme", project_count=8
    )
    beyond_cap = score_relationship(
        profile, entity_type="architect", entity_name="Acme", project_count=20
    )
    assert at_cap.score == beyond_cap.score == 61
    assert (
        next(b.points for b in at_cap.breakdown if b.factor == "similar_projects")
        == next(
            b.points for b in beyond_cap.breakdown if b.factor == "similar_projects"
        )
        == 40
    )


def test_relationship_related_tender_count_defaults_to_zero():
    profile = _make_profile()
    explicit_zero = score_relationship(
        profile, entity_type="architect", entity_name="Acme", project_count=3
    )
    passed_zero = score_relationship(
        profile,
        entity_type="architect",
        entity_name="Acme",
        project_count=3,
        related_tender_count=0,
    )
    assert explicit_zero.score == passed_zero.score
    assert (
        next(
            b.points
            for b in explicit_zero.breakdown
            if b.factor == "contract_award_signal"
        )
        == 0
    )


def test_relationship_score_increases_with_active_tender_signals():
    profile = _make_profile()
    scores = [
        score_relationship(
            profile,
            entity_type="architect",
            entity_name="Acme",
            project_count=0,
            related_tender_count=tc,
        ).score
        for tc in (0, 1, 2, 4)
    ]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]
    assert scores == [37, 45, 52, 67]


def test_relationship_tender_raw_capped_at_100():
    """related_tender_count=4 already saturates tender_raw at 100
    (4 * 25 = 100); related_tender_count=10 must produce an identical
    score, proving the cap."""
    profile = _make_profile()
    at_cap = score_relationship(
        profile,
        entity_type="architect",
        entity_name="Acme",
        project_count=0,
        related_tender_count=4,
    )
    beyond_cap = score_relationship(
        profile,
        entity_type="architect",
        entity_name="Acme",
        project_count=0,
        related_tender_count=10,
    )
    assert at_cap.score == beyond_cap.score == 67
    assert (
        next(b.points for b in at_cap.breakdown if b.factor == "contract_award_signal")
        == next(
            b.points
            for b in beyond_cap.breakdown
            if b.factor == "contract_award_signal"
        )
        == 30
    )


def test_relationship_final_score_never_exceeds_100():
    """min(100, fit) invariant. Note: with the current constant
    capability_fit contribution (21 pts) and the two capped factors'
    maxima (40 + 30 pts), the actual reachable maximum is 91, not 100 --
    this test proves the <=100 invariant holds, not that 100 itself is
    reachable (it structurally isn't, given today's weights)."""
    profile = _make_profile()
    maximal = score_relationship(
        profile,
        entity_type="architect",
        entity_name="Acme",
        project_count=8,
        related_tender_count=4,
    )
    assert maximal.score == 91
    assert maximal.score <= 100


def test_relationship_score_label_and_rank_key():
    profile = _make_profile()
    result = score_relationship(
        profile, entity_type="architect", entity_name="Acme", project_count=2
    )
    assert result.score_label == "Relationship Score"
    assert result.rank_key == float(result.score)


def test_relationship_breakdown_contains_required_factors():
    profile = _make_profile()
    result = score_relationship(
        profile, entity_type="architect", entity_name="Acme", project_count=2
    )
    factors = {b.factor for b in result.breakdown}
    assert factors == {"capability_fit", "similar_projects", "contract_award_signal"}


def test_relationship_reasons_contain_entity_type_and_name():
    profile = _make_profile()
    result = score_relationship(
        profile,
        entity_type="repeat client",
        entity_name="City of Burnaby",
        project_count=2,
    )
    assert "Repeat repeat client: City of Burnaby" in result.reasons


def test_relationship_deterministic_for_identical_inputs():
    profile = _make_profile()
    r1 = score_relationship(
        profile,
        entity_type="architect",
        entity_name="Acme Architects",
        project_count=3,
        related_tender_count=1,
    )
    r2 = score_relationship(
        profile,
        entity_type="architect",
        entity_name="Acme Architects",
        project_count=3,
        related_tender_count=1,
    )
    assert r1.score == r2.score
    assert r1.rank_key == r2.rank_key
    assert r1.reasons == r2.reasons
    assert [b.to_dict() for b in r1.breakdown] == [b.to_dict() for b in r2.breakdown]


# ===================================================================
# score_growth_tender()
# ===================================================================


def test_growth_adjacent_trade_gets_matching_capability_score():
    """profile.primary_trade='concrete'; opp trade 'structural' is in
    ADJACENT_TRADES['concrete'], so in_adjacent=True and cap_raw is
    computed via capability_match_score (60, via its own adjacent-set
    branch), not the 35 fallback."""
    profile = _make_profile(primary_trade="concrete", trade_tags=["concrete"])
    opp = _make_opp(trade_tags=["structural"])
    result = score_growth_tender(profile, opp)
    assert result.score == 55
    cap_pts = next(b.points for b in result.breakdown if b.factor == "capability_fit")
    assert cap_pts == 21  # round(60/100 * 35)


def test_growth_nonadjacent_trade_uses_fallback_cap_raw_35():
    """'roofing' is not in ADJACENT_TRADES['concrete'] -> in_adjacent
    False -> cap_raw falls back to the literal 35, never calling
    capability_match_score at all."""
    profile = _make_profile(primary_trade="concrete", trade_tags=["concrete"])
    opp = _make_opp(trade_tags=["roofing"])
    result = score_growth_tender(profile, opp)
    assert result.score == 46
    cap_pts = next(b.points for b in result.breakdown if b.factor == "capability_fit")
    assert cap_pts == 12  # round(35/100 * 35)


def test_growth_empty_trade_tags_handled_correctly():
    """Empty opp.trade_tags -> any(...) over an empty iterable is always
    False -> in_adjacent=False -> same 35 fallback as the non-adjacent
    case (identical score to test_growth_nonadjacent_trade_uses_fallback_cap_raw_35)."""
    profile = _make_profile(primary_trade="concrete", trade_tags=["concrete"])
    opp = _make_opp(trade_tags=[])
    result = score_growth_tender(profile, opp)
    assert result.score == 46
    cap_pts = next(b.points for b in result.breakdown if b.factor == "capability_fit")
    assert cap_pts == 12


def test_growth_empty_trade_tags_reason_uses_related_work():
    profile = _make_profile(primary_trade="concrete", trade_tags=["concrete"])
    opp = _make_opp(trade_tags=[])
    result = score_growth_tender(profile, opp)
    assert result.reasons[0] == "Adjacent expansion: related work"


def test_growth_final_score_capped_at_85():
    """min(85, fit) invariant. Note: project_type_fit/similar_projects/
    budget_fit are hardcoded constants in this function (55/50/50 raw),
    contributing a fixed 34 pts regardless of input; combined with
    capability_fit's own maximum of 35 pts (when capability_match_score
    returns its ceiling of 100), the actual reachable maximum is 69, not
    85. This test proves the <=85 invariant holds even at the strongest
    achievable input, not that 85 itself is reachable (it structurally
    isn't, given today's weights)."""
    profile = _make_profile(primary_trade="concrete", trade_tags=["concrete"])
    # trade_tags includes both an adjacent trade (so in_adjacent=True,
    # routing through capability_match_score rather than the 35 fallback)
    # and the profile's own primary_trade (so capability_match_score hits
    # its "company_primary in opp_tags" branch, returning its ceiling of
    # 100) -- this is the strongest achievable input for this function.
    opp = _make_opp(trade_tags=["structural", "concrete"])
    result = score_growth_tender(profile, opp)
    assert result.score <= 85
    assert result.score == 69  # documented actual ceiling given current weights
    cap_pts = next(b.points for b in result.breakdown if b.factor == "capability_fit")
    assert cap_pts == 35  # round(100/100 * 35) -- capability_match_score ceiling


def test_growth_score_label_and_rank_key():
    profile = _make_profile()
    opp = _make_opp(trade_tags=["structural"])
    result = score_growth_tender(profile, opp)
    assert result.score_label == "Growth Opportunity Score"
    assert result.rank_key == float(result.score)


def test_growth_breakdown_contains_required_factors():
    profile = _make_profile()
    opp = _make_opp(trade_tags=["structural"])
    result = score_growth_tender(profile, opp)
    factors = {b.factor for b in result.breakdown}
    assert factors == {
        "capability_fit",
        "project_type_fit",
        "similar_projects",
        "budget_fit",
    }


def test_growth_reasons_contain_adjacent_expansion():
    profile = _make_profile()
    opp = _make_opp(trade_tags=["structural"])
    result = score_growth_tender(profile, opp)
    assert result.reasons[0] == "Adjacent expansion: structural"


def test_growth_deterministic_for_identical_inputs():
    profile = _make_profile()
    opp = _make_opp(trade_tags=["structural"])
    r1 = score_growth_tender(profile, opp)
    r2 = score_growth_tender(profile, opp)
    assert r1.score == r2.score
    assert r1.rank_key == r2.rank_key
    assert r1.reasons == r2.reasons
    assert [b.to_dict() for b in r1.breakdown] == [b.to_dict() for b in r2.breakdown]


# ===================================================================
# Regression protection: full golden breakdown/reasons snapshots
# ===================================================================


def test_golden_full_breakdown_relationship_pc0_tc0():
    """Exact breakdown snapshot (points, max_points, detail per factor)
    for the project_count=0/related_tender_count=0 baseline."""
    profile = _make_profile()
    result = score_relationship(
        profile, entity_type="architect", entity_name="Acme Architects", project_count=0
    )
    assert [b.to_dict() for b in result.breakdown] == [
        {
            "factor": "capability_fit",
            "label": "Trade alignment",
            "points": 21,
            "max_points": 30,
            "detail": "",
        },
        {
            "factor": "similar_projects",
            "label": "Shared project history",
            "points": 16,
            "max_points": 40,
            "detail": "0 projects",
        },
        {
            "factor": "contract_award_signal",
            "label": "Linked active opportunities",
            "points": 0,
            "max_points": 30,
            "detail": "",
        },
    ]
    assert result.reasons == [
        "Repeat architect: Acme Architects",
        "Trade alignment",
        "Shared project history",
    ]


def test_golden_full_breakdown_growth_adjacent():
    """Exact breakdown snapshot for the adjacent-trade growth case."""
    profile = _make_profile(primary_trade="concrete", trade_tags=["concrete"])
    opp = _make_opp(trade_tags=["structural"])
    result = score_growth_tender(profile, opp)
    assert [b.to_dict() for b in result.breakdown] == [
        {
            "factor": "capability_fit",
            "label": "Adjacent trade fit",
            "points": 21,
            "max_points": 35,
            "detail": "",
        },
        {
            "factor": "project_type_fit",
            "label": "Project type overlap",
            "points": 14,
            "max_points": 25,
            "detail": "",
        },
        {
            "factor": "similar_projects",
            "label": "Partial experience",
            "points": 10,
            "max_points": 20,
            "detail": "",
        },
        {
            "factor": "budget_fit",
            "label": "Size fit",
            "points": 10,
            "max_points": 20,
            "detail": "",
        },
    ]
    assert result.reasons == [
        "Adjacent expansion: structural",
        "Adjacent trade fit",
        "Project type overlap",
    ]
