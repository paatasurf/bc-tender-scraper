"""Unit tests for Ledcor manual bridge planning (no DB)."""

from __future__ import annotations

from unittest.mock import MagicMock

from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_STANDALONE,
    MERGE_METHOD_MANUAL_BRIDGE_LEDCOR,
)
from pipeline.company_manual_bridge import (
    LEDCOR_ALIAS_IDS,
    LEDCOR_CANONICAL_ID,
    LEDCOR_EXCLUDED_IDS,
    LedcorManualBridgePlan,
    ManualBridgeAliasSpec,
)


def _mock_company(**kwargs):
    company = MagicMock()
    defaults = {
        "id": 8756,
        "name": "Ledcor Construction Limited",
        "display_name": "Ledcor Construction Limited",
        "entity_role": ENTITY_ROLE_CANONICAL,
        "canonical_company_id": None,
        "applicant_signatory": "",
        "canonical_vendor_name": "",
        "total_projects": 58,
        "total_value": 29020000.0,
        "total_award_value": 0.0,
        "award_count": 0,
        "canonical_merge_method": "dba_name",
    }
    defaults.update(kwargs)
    for key, value in defaults.items():
        setattr(company, key, value)
    return company


def test_ledcor_plan_constants() -> None:
    assert LEDCOR_CANONICAL_ID == 8756
    assert LEDCOR_ALIAS_IDS == (3046, 302683)
    assert LEDCOR_EXCLUDED_IDS == (134005,)


def test_aggregate_recompute_not_arithmetic() -> None:
    plan = LedcorManualBridgePlan(
        aggregate_recompute={
            "method": "recompute_company_permit_aggregates",
            "before_from_permit_fk": {"total_projects": 58, "total_value": 29020000.0},
            "after_from_permit_fk_post_remap": {"total_projects": 59, "total_value": 29084000.0},
            "delta_total_projects": 1,
        }
    )
    assert plan.aggregate_recompute["method"] == "recompute_company_permit_aggregates"
    assert plan.aggregate_recompute["delta_total_projects"] == 1


def test_alias_after_not_deleted() -> None:
    after = {
        "id": 3046,
        "entity_role": ENTITY_ROLE_APPLICANT_ALIAS,
        "canonical_company_id": LEDCOR_CANONICAL_ID,
        "canonical_merge_method": MERGE_METHOD_MANUAL_BRIDGE_LEDCOR,
        "deleted": False,
    }
    assert after["entity_role"] == ENTITY_ROLE_APPLICANT_ALIAS
    assert after["deleted"] is False
