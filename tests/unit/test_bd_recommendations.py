"""Unit tests for pipeline.bd_recommendations (PR-E3A).

Mock-based, deterministic -- never touches a database. The DB-facing
loaders (get_cip, load_active_tenders, load_pipeline_permits,
load_intelligence_awards) and the scoring/gate functions (evaluate_gates,
compute_bps, intel_actionability_gate, score_relationship) are
monkeypatched at the pipeline.bd_recommendations module level, since
that module binds them via `from ... import ...` at import time. Their
own correctness is covered elsewhere (test_fit_gates.py, test_bps.py,
test_relationship_growth.py); this file tests bd_recommendations.py's
own orchestration logic. Read-only against pipeline/bd_recommendations.py
-- production logic is never modified.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pipeline.bd_recommendations as bdr
from pipeline.cip_schema import (
    CompanyIntelligenceProfile,
    GeoConcentration,
    ValueRange,
)
from pipeline.fit.dimensions import FitDimension
from pipeline.fit.gates import GateResult
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.bps import BPSResult
from pipeline.scoring.explain import BreakdownFactor, ScoredRecommendation

# ===================================================================
# Fixture builders
# ===================================================================


def _make_cip(**overrides) -> CompanyIntelligenceProfile:
    defaults = dict(
        version=2,
        computed_at="2026-01-01T00:00:00+00:00",
        company_id=1,
        kind="construction",
        name="Test Co",
        company_type="General Contractor",
        entity_class="contractor",
        primary_trade="concrete",
        secondary_trades=[],
        trade_sources=["permits"],
        specialization_confidence=0.8,
        delivery_types=["new_build"],
        normalized_project_types=["building"],
        sector_focus={"institutional": 0.6},
        dominant_sector="institutional",
        sector_confidence="high",
        work_orientation="construction",
        buyer_types=["municipal"],
        client_types=[],
        public_private_ratio=0.5,
        procurement_affinity="project",
        service_cities=["Vancouver"],
        neighborhoods=[],
        concentration_map=[
            GeoConcentration(geo="Vancouver", share=0.8, project_count=40)
        ],
        geographic_reach="local",
        value_range=ValueRange(
            p25=200_000, median=500_000, p75=1_000_000, max=2_000_000
        ),
        typical_project_value=500_000,
        deal_size_band="medium",
        project_clusters=[],
        own_permit_count=40,
        award_count=5,
        award_categories=["Construction"],
        award_clients=["City of Vancouver"],
        architect_partners=[],
        repeat_clients=[],
        growth_direction=[],
        expansion_confidence=0.3,
        profile_completeness=0.7,
        normalized_name="testco",
    )
    defaults.update(overrides)
    return CompanyIntelligenceProfile(**defaults)


def _make_opp(**overrides) -> NormalizedOpportunity:
    defaults = dict(
        category="active",
        subtype="tender",
        source_table="tenders",
        source_id=1,
        title="Test Tender",
        organization="City of Vancouver",
        text_blob="Test Tender",
        trade_tags=["concrete"],
        project_type_tags=["building"],
        market_segment="public",
        estimated_value=500_000.0,
        geography_text="Vancouver, BC",
        deadline="2026-12-31",
        is_open=True,
        payload={"title": "Test Tender", "company": "City of Vancouver"},
    )
    defaults.update(overrides)
    return NormalizedOpportunity(**defaults)


def _gate(passed=True, rejection_code="", rejection_detail="", fits=None) -> GateResult:
    if fits is None:
        fits = {"business_fit": FitDimension("business_fit", 80, True, "ok")}
    return GateResult(
        passed=passed,
        fits=fits,
        rejection_code=rejection_code,
        rejection_detail=rejection_detail,
        failed_dimensions=[rejection_code] if not passed and rejection_code else None,
    )


def _bps(
    score=80, rank_key=None, breakdown=None, reasons=None, pursuit_verdict="Pursue"
) -> BPSResult:
    if breakdown is None:
        breakdown = [
            BreakdownFactor(
                factor="business_fit",
                label="Business Fit",
                points=score,
                max_points=100,
                detail="ok",
            )
        ]
    if reasons is None:
        reasons = ["Business Fit"]
    return BPSResult(
        score=score,
        rank_key=float(rank_key if rank_key is not None else score),
        breakdown=breakdown,
        reasons=reasons,
        pursuit_verdict=pursuit_verdict,
    )


def _always_pass_gate(cip, opp, section):
    return _gate(passed=True)


def _gate_rejecting(rejected_ids: set[int]):
    def _fn(cip, opp, section):
        if opp.source_id in rejected_ids:
            return _gate(
                passed=False,
                rejection_code="TEST_GATE_REJECT",
                rejection_detail="rejected for test",
            )
        return _gate(passed=True)

    return _fn


def _bps_by_id(scores: dict[int, int], default: int = 80):
    def _fn(cip, opp, fits, *, section):
        return _bps(score=scores.get(opp.source_id, default))

    return _fn


def _always_actionable(cip, opp, *, active_items, related_client=False):
    return True, "linked"


def _never_actionable(cip, opp, *, active_items, related_client=False):
    return False, "not linked to any active pursuit"


def _stub_score_relationship(
    scores_by_name: dict[str, int] | None = None, default: int = 80
):
    scores_by_name = scores_by_name or {}

    def _fn(
        profile, *, entity_type, entity_name, project_count, related_tender_count=0
    ):
        score = scores_by_name.get(entity_name, default)
        return ScoredRecommendation(
            score=score,
            score_label="Relationship Score",
            rank_key=float(score),
            breakdown=[],
            reasons=[f"Repeat {entity_type}: {entity_name}"],
        )

    return _fn


def _patch_loaders(
    monkeypatch,
    *,
    cip=None,
    tenders=None,
    permits=None,
    awards=None,
):
    cip = cip or _make_cip()
    get_cip_mock = MagicMock(return_value=cip)
    load_active_tenders_mock = MagicMock(return_value=tenders or [])
    load_pipeline_permits_mock = MagicMock(return_value=permits or [])
    load_intelligence_awards_mock = MagicMock(return_value=awards or [])
    monkeypatch.setattr(bdr, "get_cip", get_cip_mock)
    monkeypatch.setattr(bdr, "load_active_tenders", load_active_tenders_mock)
    monkeypatch.setattr(bdr, "load_pipeline_permits", load_pipeline_permits_mock)
    monkeypatch.setattr(bdr, "load_intelligence_awards", load_intelligence_awards_mock)
    return {
        "get_cip": get_cip_mock,
        "load_active_tenders": load_active_tenders_mock,
        "load_pipeline_permits": load_pipeline_permits_mock,
        "load_intelligence_awards": load_intelligence_awards_mock,
    }


def _patch_scoring(
    monkeypatch,
    *,
    evaluate_gates=None,
    compute_bps=None,
    intel_actionability_gate=None,
    score_relationship=None,
):
    monkeypatch.setattr(bdr, "evaluate_gates", evaluate_gates or _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", compute_bps or _bps_by_id({}))
    monkeypatch.setattr(
        bdr, "intel_actionability_gate", intel_actionability_gate or _always_actionable
    )
    monkeypatch.setattr(
        bdr, "score_relationship", score_relationship or _stub_score_relationship()
    )


# ===================================================================
# recommend_bd_intelligence()
# ===================================================================


def test_recommend_returns_all_five_sections(monkeypatch):
    _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    for key in (
        "active_opportunities",
        "market_pipeline",
        "competitive_intelligence",
        "relationship_opportunities",
        "growth_opportunities",
    ):
        assert key in result


def test_recommend_engine_version_business_fit_v3(monkeypatch):
    _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    assert result["engine_version"] == "business_fit_v3"


def test_recommend_construction_path_loads_award_pool(monkeypatch):
    mocks = _patch_loaders(monkeypatch, cip=_make_cip(kind="construction"))
    _patch_scoring(monkeypatch)
    bdr.recommend_bd_intelligence(MagicMock(), company_id=1, kind="construction")
    mocks["load_intelligence_awards"].assert_called_once()


def test_recommend_architecture_path_skips_award_pool(monkeypatch):
    mocks = _patch_loaders(monkeypatch, cip=_make_cip(kind="architecture"))
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(
        MagicMock(), company_id=1, kind="architecture"
    )
    mocks["load_intelligence_awards"].assert_not_called()
    assert result["competitive_intelligence"]["items"] == []


def test_recommend_loads_tender_permit_pools_for_both_kinds(monkeypatch):
    for kind in ("construction", "architecture"):
        mocks = _patch_loaders(monkeypatch, cip=_make_cip(kind=kind))
        _patch_scoring(monkeypatch)
        bdr.recommend_bd_intelligence(MagicMock(), company_id=1, kind=kind)
        mocks["load_active_tenders"].assert_called_once()
        mocks["load_pipeline_permits"].assert_called_once()


def test_recommend_active_limit_truncates_items(monkeypatch):
    tenders = [_make_opp(source_id=i) for i in range(1, 6)]
    _patch_loaders(monkeypatch, tenders=tenders)
    _patch_scoring(monkeypatch, compute_bps=_bps_by_id({}, default=80))
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1, active_limit=2)
    assert len(result["active_opportunities"]["items"]) == 2


def test_recommend_pipeline_limit_truncates_items(monkeypatch):
    permits = [_make_opp(source_id=i, subtype="permit") for i in range(1, 6)]
    _patch_loaders(monkeypatch, permits=permits)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1, pipeline_limit=1)
    assert len(result["market_pipeline"]["items"]) == 1


def test_recommend_intel_limit_truncates_items(monkeypatch):
    awards = [_make_opp(source_id=i, subtype="award") for i in range(1, 6)]
    _patch_loaders(monkeypatch, awards=awards, tenders=[])
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1, intel_limit=2)
    assert len(result["competitive_intelligence"]["items"]) == 2


def test_recommend_relationship_limit_truncates_items(monkeypatch):
    """Both architect_partners and repeat_clients candidates are strong
    matches (related tender + score >= 60); with relationship_limit=2 the
    section must show exactly 2, not the 4 that would otherwise qualify."""
    cip = _make_cip(
        architect_partners=[
            {"name": "Arch A", "project_count": 5},
            {"name": "Arch B", "project_count": 5},
        ],
        repeat_clients=["Client A", "Client B"],
    )
    tenders = [
        _make_opp(source_id=1, payload={"title": "Arch A tower", "company": "x"}),
        _make_opp(source_id=2, payload={"title": "Arch B tower", "company": "x"}),
        _make_opp(
            source_id=3, payload={"title": "Client A centre", "company": "Client A"}
        ),
        _make_opp(
            source_id=4, payload={"title": "Client B centre", "company": "Client B"}
        ),
    ]
    _patch_loaders(monkeypatch, cip=cip, tenders=tenders)
    _patch_scoring(monkeypatch, score_relationship=_stub_score_relationship(default=80))
    result = bdr.recommend_bd_intelligence(
        MagicMock(), company_id=1, relationship_limit=2
    )
    assert len(result["relationship_opportunities"]["items"]) == 2


def test_recommend_growth_limit_truncates_items(monkeypatch):
    tenders = [_make_opp(source_id=i) for i in range(1, 6)]
    _patch_loaders(monkeypatch, tenders=tenders)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1, growth_limit=1)
    assert len(result["growth_opportunities"]["items"]) == 1


def test_recommend_min_bps_override(monkeypatch):
    _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1, min_bps=42)
    assert result["active_opportunities"]["threshold"] == 42
    assert result["summary"]["active_bps_threshold"] == 42


def test_recommend_min_bps_defaults_to_active_threshold(monkeypatch):
    from pipeline.fit.gates import ACTIVE_BPS_THRESHOLD

    _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    assert result["active_opportunities"]["threshold"] == ACTIVE_BPS_THRESHOLD
    assert result["summary"]["active_bps_threshold"] == ACTIVE_BPS_THRESHOLD


def test_recommend_forwards_refresh_profile(monkeypatch):
    mocks = _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    bdr.recommend_bd_intelligence(MagicMock(), company_id=1, refresh_profile=True)
    _, kwargs = mocks["get_cip"].call_args
    assert kwargs["refresh"] is True


def test_recommend_forwards_max_candidates(monkeypatch):
    mocks = _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    bdr.recommend_bd_intelligence(MagicMock(), company_id=1, max_candidates=100)
    assert mocks["load_active_tenders"].call_args.kwargs["limit"] == 100
    assert mocks["load_pipeline_permits"].call_args.kwargs["limit"] == 50
    assert mocks["load_intelligence_awards"].call_args.kwargs["limit"] == 50


def test_recommend_forwards_include_closed(monkeypatch):
    mocks = _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    bdr.recommend_bd_intelligence(MagicMock(), company_id=1, include_closed=True)
    _, kwargs = mocks["load_active_tenders"].call_args
    assert kwargs["include_closed"] is True


def test_recommend_include_rejections_false_omits_key(monkeypatch):
    _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(
        MagicMock(), company_id=1, include_rejections=False
    )
    assert "rejections" not in result


def test_recommend_include_rejections_true_includes_key(monkeypatch):
    tenders = [_make_opp(source_id=1)]
    _patch_loaders(monkeypatch, tenders=tenders)
    _patch_scoring(monkeypatch, evaluate_gates=_gate_rejecting({1}))
    result = bdr.recommend_bd_intelligence(
        MagicMock(), company_id=1, include_rejections=True
    )
    assert "rejections" in result
    assert isinstance(result["rejections"], list)
    assert len(result["rejections"]) <= 25


def test_recommend_global_cap_invariant_matches_section_caps_sum(monkeypatch):
    """SECTION_CAPS (5+5+5+3+2) sums to exactly GLOBAL_CAP=20, so
    min(total_shown, GLOBAL_CAP) is a no-op given today's constants --
    this proves the <=20 invariant holds via the natural per-section-cap
    ceiling, not via the min() truncating anything."""
    tenders = [_make_opp(source_id=i) for i in range(1, 8)]
    permits = [_make_opp(source_id=100 + i, subtype="permit") for i in range(1, 8)]
    awards = [_make_opp(source_id=200 + i, subtype="award") for i in range(1, 8)]
    cip = _make_cip(
        architect_partners=[{"name": f"Arch {i}", "project_count": 5} for i in range(5)]
    )
    _patch_loaders(
        monkeypatch, cip=cip, tenders=tenders, permits=permits, awards=awards
    )
    _patch_scoring(monkeypatch, score_relationship=_stub_score_relationship(default=80))
    result = bdr.recommend_bd_intelligence(
        MagicMock(),
        company_id=1,
        active_limit=100,
        pipeline_limit=100,
        intel_limit=100,
        relationship_limit=100,
        growth_limit=100,
    )
    assert result["summary"]["total_shown"] <= bdr.GLOBAL_CAP
    assert result["summary"]["total_shown"] == 20


def test_recommend_empty_pools_produce_empty_items_and_reasons(monkeypatch):
    cip = _make_cip(dominant_sector="institutional", primary_trade="concrete")
    _patch_loaders(monkeypatch, cip=cip, tenders=[], permits=[], awards=[])
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    assert result["active_opportunities"]["items"] == []
    assert result["active_opportunities"]["empty_reason"] == (
        "No active tenders passed business-fit gates for institutional concrete work in your geography."
    )
    assert result["market_pipeline"]["items"] == []
    assert result["market_pipeline"]["empty_reason"] == (
        "No permit signals met quality threshold in your sectors and service areas."
    )
    assert result["competitive_intelligence"]["items"] == []
    assert result["competitive_intelligence"]["empty_reason"] == (
        "No contract awards linked to active pursuits or known clients."
    )
    assert result["growth_opportunities"]["items"] == []
    assert result["growth_opportunities"]["empty_reason"] == (
        "No expansion opportunities supported by company history."
    )
    assert result["relationship_opportunities"]["items"] == []
    # relationship_opportunities never passes empty_reason= at all in
    # recommend_bd_intelligence -- always None regardless of items.
    assert result["relationship_opportunities"]["empty_reason"] is None


def test_recommend_relationship_architect_included_when_related_and_score_ok(
    monkeypatch,
):
    cip = _make_cip(
        architect_partners=[{"name": "Acme Architects", "project_count": 5}]
    )
    tenders = [
        _make_opp(
            source_id=1, payload={"title": "Acme Architects tower", "company": "City"}
        )
    ]
    _patch_loaders(monkeypatch, cip=cip, tenders=tenders)
    _patch_scoring(
        monkeypatch,
        score_relationship=_stub_score_relationship({"Acme Architects": 75}),
    )
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    names = [i["entity_name"] for i in result["relationship_opportunities"]["items"]]
    assert "Acme Architects" in names


def test_recommend_relationship_architect_rejected_when_no_link_and_low_count(
    monkeypatch,
):
    """No related tender AND project_count < 2 -> skipped before scoring."""
    cip = _make_cip(
        architect_partners=[{"name": "Low Signal Architects", "project_count": 1}]
    )
    _patch_loaders(monkeypatch, cip=cip, tenders=[])
    _patch_scoring(monkeypatch, score_relationship=_stub_score_relationship(default=90))
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    names = [i["entity_name"] for i in result["relationship_opportunities"]["items"]]
    assert "Low Signal Architects" not in names


def test_recommend_relationship_architect_rejected_when_score_below_60(monkeypatch):
    cip = _make_cip(
        architect_partners=[{"name": "Weak Architects", "project_count": 5}]
    )
    tenders = [
        _make_opp(
            source_id=1, payload={"title": "Weak Architects annex", "company": "x"}
        )
    ]
    _patch_loaders(monkeypatch, cip=cip, tenders=tenders)
    _patch_scoring(
        monkeypatch,
        score_relationship=_stub_score_relationship({"Weak Architects": 40}),
    )
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    names = [i["entity_name"] for i in result["relationship_opportunities"]["items"]]
    assert "Weak Architects" not in names


def test_recommend_relationship_client_included_when_related(monkeypatch):
    cip = _make_cip(repeat_clients=["City of Burnaby"])
    tenders = [
        _make_opp(
            source_id=1,
            payload={"title": "Burnaby community centre", "company": "City of Burnaby"},
        )
    ]
    _patch_loaders(monkeypatch, cip=cip, tenders=tenders)
    _patch_scoring(
        monkeypatch,
        score_relationship=_stub_score_relationship({"City of Burnaby": 70}),
    )
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    names = [i["entity_name"] for i in result["relationship_opportunities"]["items"]]
    assert "City of Burnaby" in names


def test_recommend_relationship_client_rejected_when_no_related_tender(monkeypatch):
    """Unlike architects, clients require a non-empty `related` list
    unconditionally -- project_count is not consulted for clients."""
    cip = _make_cip(repeat_clients=["Unlinked City"])
    _patch_loaders(monkeypatch, cip=cip, tenders=[])
    _patch_scoring(monkeypatch, score_relationship=_stub_score_relationship(default=90))
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    names = [i["entity_name"] for i in result["relationship_opportunities"]["items"]]
    assert "Unlinked City" not in names


def test_recommend_cip_and_capability_profile_summary_stable_structure(monkeypatch):
    _patch_loaders(monkeypatch)
    _patch_scoring(monkeypatch)
    result = bdr.recommend_bd_intelligence(MagicMock(), company_id=1)
    expected_keys = {
        "primary_trade",
        "secondary_trades",
        "trade_tags",
        "trade_confidence",
        "specialization_confidence",
        "company_type",
        "entity_class",
        "dominant_sector",
        "sector_focus",
        "work_orientation",
        "geographic_reach",
        "avg_project_value",
        "avg_award_value",
        "value_range",
        "market_segments",
        "service_cities",
        "project_types",
        "delivery_types",
        "project_clusters",
        "profile_completeness",
        "own_permit_count",
        "award_count",
        "growth_direction",
    }
    assert set(result["company_intelligence_profile"].keys()) == expected_keys
    assert result["company_intelligence_profile"] == result["capability_profile"]


# ===================================================================
# _process_pool()
# ===================================================================


def test_process_pool_gate_rejection_excludes_item(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _gate_rejecting({1}))
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({}))
    cip = _make_cip()
    items, stats, _ = bdr._process_pool(
        cip, [_make_opp(source_id=1)], "active", bps_threshold=50
    )
    assert items == []
    assert stats == {
        "scanned": 1,
        "gate_rejected": 1,
        "bps_rejected": 0,
        "scored": 0,
        "shown": 0,
    }


def test_process_pool_bps_threshold_rejection_excludes_item(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({1: 30}))
    cip = _make_cip()
    items, stats, _ = bdr._process_pool(
        cip, [_make_opp(source_id=1)], "active", bps_threshold=50
    )
    assert items == []
    assert stats == {
        "scanned": 1,
        "gate_rejected": 0,
        "bps_rejected": 1,
        "scored": 1,
        "shown": 0,
    }


def test_process_pool_accepted_item_is_shown(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({1: 90}))
    cip = _make_cip()
    items, stats, _ = bdr._process_pool(
        cip, [_make_opp(source_id=1)], "active", bps_threshold=50
    )
    assert len(items) == 1
    assert items[0]["score"] == 90
    assert items[0]["item_type"] == "tender"
    assert stats["shown"] == 1


def test_process_pool_sorts_by_rank_key_descending(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({1: 60, 2: 90, 3: 75}))
    cip = _make_cip()
    pool = [_make_opp(source_id=i) for i in (1, 2, 3)]
    items, _, _ = bdr._process_pool(cip, pool, "active", bps_threshold=50)
    assert [i["score"] for i in items] == [90, 75, 60]


def test_process_pool_respects_section_cap(monkeypatch):
    """growth section cap is 2 -- more accepted items must be truncated
    to the top 2 by rank_key."""
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({1: 60, 2: 90, 3: 75, 4: 50}))
    cip = _make_cip()
    pool = [_make_opp(source_id=i) for i in (1, 2, 3, 4)]
    items, stats, _ = bdr._process_pool(cip, pool, "growth", bps_threshold=40)
    assert len(items) == 2
    assert [i["score"] for i in items] == [90, 75]
    assert stats["shown"] == 2
    assert stats["scored"] == 4


def test_process_pool_intelligence_actionability_gate_rejects(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({}))
    monkeypatch.setattr(bdr, "intel_actionability_gate", _never_actionable)
    cip = _make_cip()
    items, stats, rejections = bdr._process_pool(
        cip,
        [_make_opp(source_id=1)],
        "intelligence",
        bps_threshold=50,
        active_items=[],
        include_rejections=True,
    )
    assert items == []
    assert stats["gate_rejected"] == 1
    assert stats["scored"] == 0
    assert rejections[0]["rejection_code"] == "INTEL_NOT_ACTIONABLE"


def test_process_pool_intelligence_actionability_gate_passes(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({1: 90}))
    monkeypatch.setattr(bdr, "intel_actionability_gate", _always_actionable)
    cip = _make_cip()
    items, stats, _ = bdr._process_pool(
        cip,
        [_make_opp(source_id=1)],
        "intelligence",
        bps_threshold=50,
        active_items=[],
    )
    assert len(items) == 1
    assert items[0]["item_type"] == "contract_award"
    assert "related_active_tender_ids" in items[0]


def test_process_pool_growth_marker_present_only_for_growth_section(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _always_pass_gate)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({1: 90}))
    cip = _make_cip()

    growth_items, _, _ = bdr._process_pool(
        cip, [_make_opp(source_id=1)], "growth", bps_threshold=40
    )
    assert growth_items[0]["growth"] is True

    active_items, _, _ = bdr._process_pool(
        cip, [_make_opp(source_id=1)], "active", bps_threshold=40
    )
    assert "growth" not in active_items[0]


def test_process_pool_stats_scanned_rejected_scored_shown(monkeypatch):
    """Mixed batch: 1 gate-rejected, 1 bps-rejected, 1 accepted."""

    def gate_fn(cip, opp, section):
        if opp.source_id == 1:
            return _gate(passed=False, rejection_code="R", rejection_detail="d")
        return _gate(passed=True)

    monkeypatch.setattr(bdr, "evaluate_gates", gate_fn)
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({2: 20, 3: 90}))
    cip = _make_cip()
    pool = [_make_opp(source_id=i) for i in (1, 2, 3)]
    items, stats, _ = bdr._process_pool(cip, pool, "active", bps_threshold=50)
    assert stats == {
        "scanned": 3,
        "gate_rejected": 1,
        "bps_rejected": 1,
        "scored": 2,
        "shown": 1,
    }
    assert len(items) == 1
    assert items[0]["score"] == 90


def test_process_pool_rejection_details_capped_at_ten(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _gate_rejecting(set(range(1, 16))))
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({}))
    cip = _make_cip()
    pool = [_make_opp(source_id=i) for i in range(1, 16)]
    _, stats, rejections = bdr._process_pool(
        cip, pool, "active", bps_threshold=50, include_rejections=True
    )
    assert stats["gate_rejected"] == 15
    assert len(rejections) == 10


def test_process_pool_no_rejection_details_when_disabled(monkeypatch):
    monkeypatch.setattr(bdr, "evaluate_gates", _gate_rejecting({1, 2, 3}))
    monkeypatch.setattr(bdr, "compute_bps", _bps_by_id({}))
    cip = _make_cip()
    pool = [_make_opp(source_id=i) for i in (1, 2, 3)]
    _, stats, rejections = bdr._process_pool(
        cip, pool, "active", bps_threshold=50, include_rejections=False
    )
    assert stats["gate_rejected"] == 3
    assert rejections == []


# ===================================================================
# _related_tender_ids()
# ===================================================================


def test_related_tender_ids_matches_on_title():
    active = [
        {
            "id": 1,
            "payload": {"title": "Vancouver Community Centre Expansion", "company": ""},
        }
    ]
    ids = bdr._related_tender_ids(active, "Community Centre project")
    assert ids == [1]


def test_related_tender_ids_matches_on_organization():
    active = [{"id": 2, "payload": {"title": "", "company": "City of Burnaby"}}]
    ids = bdr._related_tender_ids(active, "Burnaby infrastructure")
    assert ids == [2]


def test_related_tender_ids_case_insensitive():
    active = [
        {"id": 3, "payload": {"title": "VANCOUVER SCHOOL Project", "company": ""}}
    ]
    ids = bdr._related_tender_ids(active, "vancouver school renovation")
    assert ids == [3]


def test_related_tender_ids_ignores_short_tokens():
    """Tokens of length <= 4 are never checked as substrings -- a hay
    made only of short tokens must never match, even if those exact
    short strings appear in the title."""
    active = [{"id": 4, "payload": {"title": "abcd efgh school", "company": ""}}]
    ids = bdr._related_tender_ids(active, "abcd efgh")
    assert ids == []


def test_related_tender_ids_respects_limit():
    active = [
        {"id": i, "payload": {"title": "Vancouver Community project", "company": ""}}
        for i in range(1, 6)
    ]
    ids = bdr._related_tender_ids(active, "Vancouver Community", limit=2)
    assert ids == [1, 2]


def test_related_tender_ids_no_matches_returns_empty():
    active = [
        {
            "id": 1,
            "payload": {"title": "Totally unrelated project", "company": "Nobody"},
        }
    ]
    ids = bdr._related_tender_ids(active, "Something completely different")
    assert ids == []


# ===================================================================
# _item_payload(), _section(), _empty_reason()
# ===================================================================


def test_item_payload_contains_score_explanation_evidence_source():
    opp = _make_opp(
        source_id=7, organization="City of Vancouver", estimated_value=1_000_000.0
    )
    bps = _bps(score=88)
    fit_dict = {
        "business_fit": {
            "name": "business_fit",
            "score": 80,
            "passed": True,
            "reason": "ok",
        }
    }
    payload = bdr._item_payload(opp, bps, item_type="tender", fit_assessment=fit_dict)
    assert payload["score"] == 88
    assert payload["score_label"] == "Business Pursuit Score"
    assert payload["explanation"] == bps.to_explanation_dict()
    assert payload["evidence"] == {
        "buyer": "City of Vancouver",
        "expected_value": 1_000_000.0,
        "sector": opp.sector,
        "geography": opp.geography_text[:120],
    }
    assert payload["fit_assessment"] == fit_dict
    assert payload["id"] == 7


def test_item_payload_source_is_always_rules():
    opp = _make_opp()
    bps = _bps()
    payload = bdr._item_payload(opp, bps, item_type="tender", fit_assessment={})
    assert payload["source"] == "rules"


def test_item_payload_extra_merges_into_base():
    opp = _make_opp()
    bps = _bps()
    payload = bdr._item_payload(
        opp, bps, item_type="tender", fit_assessment={}, extra={"growth": True}
    )
    assert payload["growth"] is True


def test_section_totals_from_stats():
    stats = {
        "scanned": 10,
        "gate_rejected": 3,
        "bps_rejected": 2,
        "scored": 7,
        "shown": 5,
    }
    section = bdr._section(
        "Label", "Description", 65, stats, [{"x": 1}] * 5, empty_reason=None
    )
    assert section["total_scanned"] == 10
    assert section["total_gate_rejected"] == 3
    assert section["total_bps_rejected"] == 2
    assert section["total_scored"] == 7
    assert section["total_shown"] == 5
    assert section["total_candidates_evaluated"] == 10
    assert section["total_passed_filter"] == 5
    assert section["threshold"] == 65
    assert section["label"] == "Label"
    assert section["description"] == "Description"


def test_empty_reason_none_when_items_present():
    cip = _make_cip()
    assert bdr._empty_reason("active", cip, [{"x": 1}]) is None


def test_empty_reason_messages_per_section():
    cip = _make_cip(dominant_sector="commercial", primary_trade="electrical")
    assert bdr._empty_reason("active", cip, []) == (
        "No active tenders passed business-fit gates for commercial electrical work in your geography."
    )
    assert bdr._empty_reason("pipeline", cip, []) == (
        "No permit signals met quality threshold in your sectors and service areas."
    )
    assert bdr._empty_reason("intel", cip, []) == (
        "No contract awards linked to active pursuits or known clients."
    )
    assert bdr._empty_reason("growth", cip, []) == (
        "No expansion opportunities supported by company history."
    )


def test_empty_reason_unknown_section_returns_none():
    """'relationship' is not a key in the messages dict -- confirms
    recommend_bd_intelligence's relationship_opportunities section
    (which never calls _empty_reason at all) is consistent with this
    function's own behavior for an unrecognized section name."""
    cip = _make_cip()
    assert bdr._empty_reason("relationship", cip, []) is None
