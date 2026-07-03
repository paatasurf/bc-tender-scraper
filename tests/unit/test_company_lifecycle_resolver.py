"""Unit tests for company lifecycle resolver (Company Lifecycle Phase 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import cycle
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from db.company_lifecycle_constants import (
    COMPANY_LIFECYCLE_STATUS_ACTIVE,
    COMPANY_LIFECYCLE_STATUS_DORMANT,
    COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE,
    COMPANY_LIFECYCLE_STATUS_QUIET,
    is_operating_for_status,
    lifecycle_status_from_age_days,
)
from pipeline.company_lifecycle_resolver import (
    CompanyLifecycleSnapshot,
    age_days_since_activity,
    apply_company_lifecycle_transition,
    evaluate_company_lifecycle_transition,
    expected_lifecycle_for_activity,
    iso_date_to_activity_timestamp,
    resolve_company_lifecycle,
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _snap(**kwargs) -> CompanyLifecycleSnapshot:
    defaults = {
        "lifecycle_status": COMPANY_LIFECYCLE_STATUS_ACTIVE,
        "is_operating": True,
        "lifecycle_status_override": None,
        "last_activity_at": _utc(2026, 5, 1),
    }
    defaults.update(kwargs)
    return CompanyLifecycleSnapshot(**defaults)


def test_lifecycle_status_from_age_days_thresholds():
    assert lifecycle_status_from_age_days(100) == COMPANY_LIFECYCLE_STATUS_ACTIVE
    assert lifecycle_status_from_age_days(365) == COMPANY_LIFECYCLE_STATUS_ACTIVE
    assert lifecycle_status_from_age_days(366) == COMPANY_LIFECYCLE_STATUS_QUIET
    assert lifecycle_status_from_age_days(730) == COMPANY_LIFECYCLE_STATUS_QUIET
    assert lifecycle_status_from_age_days(731) == COMPANY_LIFECYCLE_STATUS_DORMANT
    assert lifecycle_status_from_age_days(None) == COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE


def test_is_operating_for_status():
    assert is_operating_for_status(COMPANY_LIFECYCLE_STATUS_ACTIVE) is True
    assert is_operating_for_status(COMPANY_LIFECYCLE_STATUS_QUIET) is True
    assert is_operating_for_status(COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE) is True
    assert is_operating_for_status(COMPANY_LIFECYCLE_STATUS_DORMANT) is False


def test_recent_activity_maps_to_active():
    last = _utc(2026, 1, 15)
    rule = evaluate_company_lifecycle_transition(
        _snap(lifecycle_status=COMPANY_LIFECYCLE_STATUS_DORMANT, is_operating=False, last_activity_at=None),
        computed_last_activity=last,
        now=_utc(2026, 7, 2),
    )
    assert rule == "status_active"


def test_12_to_24_month_activity_maps_to_quiet():
    last = _utc(2025, 1, 15)
    status, operating = expected_lifecycle_for_activity(last, now=_utc(2026, 7, 2))
    assert status == COMPANY_LIFECYCLE_STATUS_QUIET
    assert operating is True
    rule = evaluate_company_lifecycle_transition(
        _snap(lifecycle_status=COMPANY_LIFECYCLE_STATUS_ACTIVE, last_activity_at=None),
        computed_last_activity=last,
        now=_utc(2026, 7, 2),
    )
    assert rule == "status_quiet"


def test_24_month_plus_activity_maps_to_dormant():
    last = _utc(2023, 1, 1)
    rule = evaluate_company_lifecycle_transition(
        _snap(lifecycle_status=COMPANY_LIFECYCLE_STATUS_ACTIVE, last_activity_at=None),
        computed_last_activity=last,
        now=_utc(2026, 7, 2),
    )
    assert rule == "status_dormant"


def test_no_fk_activity_maps_to_no_observable_activity():
    rule = evaluate_company_lifecycle_transition(
        _snap(lifecycle_status=COMPANY_LIFECYCLE_STATUS_ACTIVE, last_activity_at=_utc(2026, 1, 1)),
        computed_last_activity=None,
        now=_utc(2026, 7, 2),
    )
    assert rule == "status_no_observable_activity"


def test_no_observable_activity_is_operating_true():
    row = MagicMock()
    apply_company_lifecycle_transition(
        row,
        "status_no_observable_activity",
        computed_last_activity=None,
        now=_utc(2026, 7, 2),
    )
    assert row.lifecycle_status == COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE
    assert row.is_operating is True
    assert row.last_activity_at is None


def test_override_precedence_skips_transition():
    rule = evaluate_company_lifecycle_transition(
        _snap(lifecycle_status_override="dormant"),
        computed_last_activity=_utc(2026, 6, 1),
        now=_utc(2026, 7, 2),
    )
    assert rule is None


def test_idempotent_when_already_correct():
    last = _utc(2026, 5, 1)
    snapshot = _snap(
        lifecycle_status=COMPANY_LIFECYCLE_STATUS_ACTIVE,
        is_operating=True,
        last_activity_at=last,
    )
    assert (
        evaluate_company_lifecycle_transition(
            snapshot,
            computed_last_activity=last,
            now=_utc(2026, 7, 2),
        )
        is None
    )


def test_iso_date_to_activity_timestamp_end_of_day():
    ts = iso_date_to_activity_timestamp("2026-05-01")
    assert ts == _utc(2026, 5, 1, 23, 59, 59)


def test_age_days_since_activity():
    assert age_days_since_activity(_utc(2026, 1, 1), now=_utc(2026, 7, 2)) == 182


def test_resolve_company_lifecycle_integration_mock():
    company = MagicMock()
    company.id = 1
    company.lifecycle_status = COMPANY_LIFECYCLE_STATUS_ACTIVE
    company.is_operating = True
    company.lifecycle_status_override = None
    company.last_activity_at = None

    award_result = MagicMock()
    award_result.all.return_value = [(1, "2026-05-01")]
    outcome_result = MagicMock()
    outcome_result.all.return_value = []
    permit_check = MagicMock()
    permit_check.first.return_value = None

    session = MagicMock()
    session.execute.side_effect = cycle([award_result, outcome_result, permit_check])
    session.scalars.return_value.all.return_value = [company]

    first = resolve_company_lifecycle(session, now=_utc(2026, 7, 2), commit=False)
    company.lifecycle_status = COMPANY_LIFECYCLE_STATUS_ACTIVE
    company.is_operating = True
    company.last_activity_at = iso_date_to_activity_timestamp("2026-05-01")
    second = resolve_company_lifecycle(session, now=_utc(2026, 7, 2), commit=False)

    assert first["totals"]["status_active"] == 1
    assert second["totals"]["status_active"] == 0
    assert second["totals"]["skipped_no_change"] == 1


def test_resolve_companies_endpoint_requires_internal_key():
    client = TestClient(app)
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        response = client.post("/internal/lifecycle/resolve-companies")
    assert response.status_code == 403


def test_resolve_companies_sync_returns_resolver_summary():
    client = TestClient(app)
    summary = {
        "resolved_at": "2026-07-02T00:00:00+00:00",
        "activity_sources": {"award_linked_companies": 2404, "outcome_linked_companies": 0, "permit_linked_companies": 0},
        "totals": {"skipped_no_change": 14139},
    }
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch(
            "pipeline.company_lifecycle_resolver.run_company_lifecycle_resolve_job",
            return_value=summary,
        ) as mock_run:
            response = client.post(
                "/internal/lifecycle/resolve-companies",
                headers={"X-Internal-Key": "secret"},
            )
    assert response.status_code == 200
    assert response.json() == summary
    mock_run.assert_called_once()


def test_resolve_companies_background_returns_started_and_runs_job():
    client = TestClient(app)
    summary = {
        "resolved_at": "2026-07-02T00:00:00+00:00",
        "activity_sources": {"award_linked_companies": 2404, "outcome_linked_companies": 0, "permit_linked_companies": 0},
        "totals": {"skipped_no_change": 14139},
    }
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch(
            "pipeline.company_lifecycle_resolver.run_company_lifecycle_resolve_job",
            return_value=summary,
        ) as mock_run:
            response = client.post(
                "/internal/lifecycle/resolve-companies?background=true",
                headers={"X-Internal-Key": "secret"},
            )
    assert response.status_code == 200
    assert response.json() == {"status": "started"}
    mock_run.assert_called_once()
