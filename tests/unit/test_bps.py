"""Unit tests for pipeline.scoring.bps -- PR-E1 (algorithm_version)."""

from __future__ import annotations

from pipeline.fit.dimensions import compute_all_fits
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.bps import BPS_ALGORITHM_VERSION, compute_bps
from tests.unit.competitive_fixtures import make_cip


def _make_opp(**overrides) -> NormalizedOpportunity:
    defaults = dict(
        category="active",
        subtype="tender",
        source_table="tenders",
        source_id=1,
        title="New Elementary School Construction",
        organization="City of Vancouver",
        text_blob="New Elementary School Construction — general building new_build institutional",
        trade_tags=["general_building"],
        project_type_tags=["building"],
        market_segment="public",
        estimated_value=500_000.0,
        geography_text="Vancouver, BC",
        deadline="2026-12-31",
        is_open=True,
        payload={},
        delivery_type="new_build",
        sector="institutional",
        buyer_type="municipal",
        orientation="construction",
    )
    defaults.update(overrides)
    return NormalizedOpportunity(**defaults)


def test_algorithm_version_present():
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="active")
    d = result.to_explanation_dict()
    assert d["algorithm_version"] == BPS_ALGORITHM_VERSION
    assert d["algorithm_version"] == "bps_v1"


def test_algorithm_version_directly_available_and_nonempty():
    """algorithm_version is a property on BPSResult itself, not only in
    to_explanation_dict() -- no need to serialize just to read it."""
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="active")
    assert result.algorithm_version
    assert isinstance(result.algorithm_version, str)
    assert result.algorithm_version == BPS_ALGORITHM_VERSION


def test_serialized_algorithm_version_equals_property():
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="active")
    assert result.to_explanation_dict()["algorithm_version"] == result.algorithm_version


def test_algorithm_version_stable_across_calls():
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    v1 = compute_bps(cip, opp, fits, section="active").to_explanation_dict()[
        "algorithm_version"
    ]
    v2 = compute_bps(cip, opp, fits, section="active").to_explanation_dict()[
        "algorithm_version"
    ]
    assert v1 == v2


def test_nested_fit_assessment_carries_dimensions_version():
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="active")
    d = result.to_explanation_dict()
    for dim in d["fit_assessment"].values():
        assert dim["algorithm_version"] == "fit_dimensions_v1"


def test_breakdown_sum_close_to_score_within_rounding():
    """Untouched, pre-existing characteristic (not an exact invariant):
    `score` is int(round(unrounded float total)), while each breakdown
    factor is rounded individually, so they may differ by a few points of
    rounding drift. This just pins that drift stays small and >= 0."""
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="active")
    component_sum = sum(b.points for b in result.breakdown)
    assert 0 <= result.score - component_sum <= 3
    assert 0 <= result.score <= 100


def test_golden_active_section_score_and_breakdown_unchanged():
    """Regression guard: fixed input -> exact score/breakdown/verdict.
    Fails if section weight tables or bonus logic in bps.py change."""
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="active")
    assert result.score == 85
    assert result.pursuit_verdict == (
        "Pursue — strong alignment across trade, sector, and geography"
    )
    breakdown_points = {b.factor: b.points for b in result.breakdown}
    assert breakdown_points == {
        "business_fit": 25,
        "project_type_fit": 17,
        "sector_fit": 13,
        "geography_fit": 13,
        "value_fit": 8,
        "client_fit": 8,
    }


def test_golden_pipeline_section_score_and_breakdown_unchanged():
    """Regression guard for the distinct 'pipeline' section weight table."""
    cip = make_cip()
    opp = _make_opp(category="pipeline", estimated_value=0.0)
    fits = compute_all_fits(cip, opp)
    result = compute_bps(cip, opp, fits, section="pipeline")
    assert result.score == 83
    breakdown_points = {b.factor: b.points for b in result.breakdown}
    assert breakdown_points == {
        "business_fit": 10,
        "project_type_fit": 21,
        "sector_fit": 17,
        "geography_fit": 22,
        "value_fit": 5,
        "client_fit": 8,
    }
