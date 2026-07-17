"""Unit tests for pipeline.fit.gates -- PR-E1 (algorithm_version)."""

from __future__ import annotations

from pipeline.fit.gates import (
    ACTIVE_BPS_THRESHOLD,
    GATE_THRESHOLDS,
    GATES_ALGORITHM_VERSION,
    GROWTH_BPS_THRESHOLD,
    INTEL_BPS_THRESHOLD,
    PIPELINE_BPS_THRESHOLD,
    evaluate_gates,
)
from pipeline.market_normalizer import NormalizedOpportunity
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


def test_algorithm_version_present_on_gate_result():
    cip = make_cip()
    opp = _make_opp()
    result = evaluate_gates(cip, opp, "active")
    d = result.to_dict()
    assert d["algorithm_version"] == GATES_ALGORITHM_VERSION
    assert d["algorithm_version"] == "fit_gates_v1"


def test_algorithm_version_directly_available_and_nonempty():
    """algorithm_version is a property on GateResult itself, not only in
    to_dict() -- no need to serialize just to read it."""
    cip = make_cip()
    opp = _make_opp()
    result = evaluate_gates(cip, opp, "active")
    assert result.algorithm_version
    assert isinstance(result.algorithm_version, str)
    assert result.algorithm_version == GATES_ALGORITHM_VERSION


def test_serialized_algorithm_version_equals_property():
    cip = make_cip()
    opp = _make_opp()
    result = evaluate_gates(cip, opp, "active")
    assert result.to_dict()["algorithm_version"] == result.algorithm_version


def test_algorithm_version_stable_across_calls():
    cip = make_cip()
    opp = _make_opp()
    v1 = evaluate_gates(cip, opp, "active").to_dict()["algorithm_version"]
    v2 = evaluate_gates(cip, opp, "active").to_dict()["algorithm_version"]
    assert v1 == v2


def test_nested_fit_dimensions_also_carry_their_own_version():
    """GateResult.to_dict() nests FitDimension.to_dict() -- each nested
    dimension keeps its own (dimensions.py) algorithm_version, distinct
    from the gate-level one."""
    cip = make_cip()
    opp = _make_opp()
    result = evaluate_gates(cip, opp, "active")
    d = result.to_dict()
    for dim in d["fits"].values():
        assert dim["algorithm_version"] == "fit_dimensions_v1"


def test_golden_active_pass_unchanged_except_additive_version_field():
    """Regression guard: fixed input -> passes with no rejection, unchanged
    decision. Fails if a gate threshold/hard-reject rule changes."""
    cip = make_cip()
    opp = _make_opp()
    result = evaluate_gates(cip, opp, "active").to_dict()
    assert result["passed"] is True
    assert result["rejection_code"] == ""
    assert result["rejection_detail"] == ""
    assert result["failed_dimensions"] == []


def test_golden_maintenance_orientation_hard_reject_unchanged():
    """Regression guard: fixed input -> ORIENTATION_MISMATCH hard reject.
    Fails if _hard_reject's maintenance-orientation rule changes."""
    cip_designer = make_cip(sector_focus={"residential": 0.9})
    opp_maintenance = _make_opp(
        orientation="maintenance", title="HVAC maintenance service contract"
    )
    result = evaluate_gates(cip_designer, opp_maintenance, "active").to_dict()
    assert result["passed"] is False
    assert result["rejection_code"] == "ORIENTATION_MISMATCH"
    assert result["rejection_detail"] == (
        "Maintenance/SOA procurement vs construction-oriented company"
    )
    assert result["algorithm_version"] == GATES_ALGORITHM_VERSION


def test_static_regression_guard_threshold_constants_unchanged():
    """Static guard: this PR must not change any gate threshold value."""
    assert ACTIVE_BPS_THRESHOLD == 65
    assert PIPELINE_BPS_THRESHOLD == 65
    assert INTEL_BPS_THRESHOLD == 68
    assert GROWTH_BPS_THRESHOLD == 72
    assert GATE_THRESHOLDS == {
        "active": {
            "business_fit": 60,
            "project_type_fit": 40,
            "sector_fit": 50,
            "geography_fit": 55,
            "value_fit": 45,
            "client_fit": 35,
        },
        "pipeline": {
            "business_fit": 45,
            "project_type_fit": 35,
            "sector_fit": 40,
            "geography_fit": 50,
            "value_fit": 0,
            "client_fit": 0,
        },
        "intelligence": {
            "business_fit": 50,
            "project_type_fit": 30,
            "sector_fit": 40,
            "geography_fit": 40,
            "value_fit": 0,
            "client_fit": 40,
        },
        "growth": {
            "business_fit": 55,
            "project_type_fit": 40,
            "sector_fit": 45,
            "geography_fit": 50,
            "value_fit": 45,
            "client_fit": 35,
        },
    }
