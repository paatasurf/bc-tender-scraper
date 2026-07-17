"""Unit tests for pipeline.fit.dimensions -- PR-E1 (algorithm_version)."""

from __future__ import annotations

from pipeline.fit.dimensions import (
    FIT_DIMENSIONS_ALGORITHM_VERSION,
    compute_all_fits,
)
from pipeline.market_normalizer import NormalizedOpportunity
from tests.unit.competitive_fixtures import make_cip

_DIMENSION_KEYS = (
    "business_fit",
    "project_type_fit",
    "sector_fit",
    "geography_fit",
    "value_fit",
    "client_fit",
)


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


# Golden expected values captured from the unedited pre-PR-E1 implementation
# (see PR-E1 verification notes) -- pins the scoring formulas/thresholds in
# this module so an unintentional change to them fails this test.
_GOLDEN_ACTIVE = {
    "business_fit": {
        "name": "business_fit",
        "score": 100,
        "passed": True,
        "reason": "Trade alignment: general_building vs general_building",
    },
    "project_type_fit": {
        "name": "project_type_fit",
        "score": 85,
        "passed": True,
        "reason": "Delivery type 'new_build' matches company history",
    },
    "sector_fit": {
        "name": "sector_fit",
        "score": 84,
        "passed": True,
        "reason": "Sector 'institutional' is 60% of company focus",
    },
    "geography_fit": {
        "name": "geography_fit",
        "score": 87,
        "passed": True,
        "reason": "Service city match: Vancouver",
    },
    "value_fit": {
        "name": "value_fit",
        "score": 85,
        "passed": True,
        "reason": "$500,000 within typical range ($100,000–$3,000,000)",
    },
    "client_fit": {
        "name": "client_fit",
        "score": 85,
        "passed": True,
        "reason": "Known client: City of Vancouver",
    },
}


def test_algorithm_version_present_in_every_dimension():
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    assert set(fits) == set(_DIMENSION_KEYS)
    for key, dim in fits.items():
        d = dim.to_dict()
        assert d["algorithm_version"] == FIT_DIMENSIONS_ALGORITHM_VERSION
        assert d["algorithm_version"] == "fit_dimensions_v1"


def test_algorithm_version_directly_available_and_nonempty():
    """algorithm_version is a property on FitDimension itself, not only in
    to_dict() -- no need to serialize just to read it."""
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    for key, dim in fits.items():
        assert dim.algorithm_version
        assert isinstance(dim.algorithm_version, str)
        assert dim.algorithm_version == FIT_DIMENSIONS_ALGORITHM_VERSION


def test_serialized_algorithm_version_equals_property():
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    for key, dim in fits.items():
        assert dim.to_dict()["algorithm_version"] == dim.algorithm_version


def test_algorithm_version_stable_across_calls():
    cip = make_cip()
    opp = _make_opp()
    v1 = compute_all_fits(cip, opp)["business_fit"].to_dict()["algorithm_version"]
    v2 = compute_all_fits(cip, opp)["business_fit"].to_dict()["algorithm_version"]
    assert v1 == v2


def test_golden_values_unchanged_except_additive_version_field():
    """Regression guard: fixed input -> exact score/passed/reason per
    dimension. Fails if any formula/threshold in dimensions.py changes."""
    cip = make_cip()
    opp = _make_opp()
    fits = compute_all_fits(cip, opp)
    for key, expected in _GOLDEN_ACTIVE.items():
        actual = fits[key].to_dict()
        actual_without_version = {
            k: v for k, v in actual.items() if k != "algorithm_version"
        }
        assert actual_without_version == expected, key


def test_pipeline_category_value_fit_neutral_when_unstated():
    cip = make_cip()
    opp = _make_opp(category="pipeline", estimated_value=0.0)
    fits = compute_all_fits(cip, opp)
    value_fit = fits["value_fit"]
    assert value_fit.score == 50
    assert value_fit.reason == "Value not stated — neutral"
    assert value_fit.to_dict()["algorithm_version"] == FIT_DIMENSIONS_ALGORITHM_VERSION
