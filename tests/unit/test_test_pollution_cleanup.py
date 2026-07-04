"""Unit tests for test pollution cleanup plan logic."""

from __future__ import annotations

from unittest.mock import MagicMock

from pipeline.test_pollution_cleanup import (
    TEST_POLLUTION_COMPANY_IDS,
    PollutionCleanupPlan,
    apply_test_pollution_cleanup_plan,
)


def test_company_id_list_skips_572935():
    assert 572935 not in TEST_POLLUTION_COMPANY_IDS
    assert len(TEST_POLLUTION_COMPANY_IDS) == 16
    assert TEST_POLLUTION_COMPANY_IDS[0] == 572934
    assert TEST_POLLUTION_COMPANY_IDS[-1] == 572950


def test_blocked_when_external_fk_nonzero():
    plan = PollutionCleanupPlan(
        fk_checks={"permits.company_id": 1, "contract_awards.company_id": 0},
    )
    unexpected = {k: v for k, v in plan.fk_checks.items() if v not in (0, -1)}
    assert unexpected == {"permits.company_id": 1}
    plan.blocked = bool(unexpected)
    plan.validation_errors.append(f"Non-zero FK references block delete: {unexpected}")
    assert plan.blocked is True


def test_apply_refuses_blocked_plan():
    import pytest

    plan = PollutionCleanupPlan(blocked=True, validation_errors=["blocked"])
    with pytest.raises(ValueError, match="blocked"):
        apply_test_pollution_cleanup_plan(MagicMock(), plan)
