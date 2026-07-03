"""Unit tests for permit lifecycle resolver (Permit Lifecycle Phase 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from db.permit_lifecycle_constants import (
    PERMIT_LIFECYCLE_STATUS_ACTIVE,
    PERMIT_LIFECYCLE_STATUS_CANCELLED,
    PERMIT_LIFECYCLE_STATUS_COMPLETED,
    PERMIT_LIFECYCLE_STATUS_STALE,
    PERMIT_LIFECYCLE_STATUS_UNKNOWN,
)
from pipeline.permit_lifecycle_resolver import (
    PermitLifecycleSnapshot,
    apply_permit_lifecycle_transition,
    evaluate_permit_lifecycle_transition,
    lifecycle_from_source_status,
    resolve_permit_lifecycle,
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _snap(**kwargs) -> PermitLifecycleSnapshot:
    defaults = {
        "lifecycle_status": PERMIT_LIFECYCLE_STATUS_ACTIVE,
        "is_active": True,
        "lifecycle_status_override": None,
        "source_status_raw": "",
        "issue_date": "2026-05-01",
        "application_date": "",
    }
    defaults.update(kwargs)
    return PermitLifecycleSnapshot(**defaults)


def test_lifecycle_from_source_status_vancouver_vocabulary():
    assert lifecycle_from_source_status("Finaled") == PERMIT_LIFECYCLE_STATUS_COMPLETED
    assert lifecycle_from_source_status("Issued") == PERMIT_LIFECYCLE_STATUS_ACTIVE
    assert lifecycle_from_source_status("Cancelled") == PERMIT_LIFECYCLE_STATUS_CANCELLED
    assert lifecycle_from_source_status("In Review") == PERMIT_LIFECYCLE_STATUS_ACTIVE


def test_source_status_finaled_maps_to_completed():
    rule = evaluate_permit_lifecycle_transition(
        _snap(source_status_raw="Finaled"),
        now=_utc(2026, 7, 2),
    )
    assert rule == "source_status_completed"


def test_source_status_cancelled_maps_to_cancelled():
    rule = evaluate_permit_lifecycle_transition(
        _snap(source_status_raw="Withdrawn"),
        now=_utc(2026, 7, 2),
    )
    assert rule == "source_status_cancelled"


def test_source_status_issued_maps_to_active():
    rule = evaluate_permit_lifecycle_transition(
        _snap(
            lifecycle_status=PERMIT_LIFECYCLE_STATUS_STALE,
            is_active=False,
            source_status_raw="Issued",
            issue_date="2020-01-01",
        ),
        now=_utc(2026, 7, 2),
    )
    assert rule == "source_status_active"


def test_age_stale_when_no_source_status_and_old_issue_date():
    rule = evaluate_permit_lifecycle_transition(
        _snap(source_status_raw="", issue_date="2022-01-01", application_date=""),
        now=_utc(2026, 7, 2),
    )
    assert rule == "age_stale_24mo"


def test_no_source_no_dates_stays_unknown_active():
    rule = evaluate_permit_lifecycle_transition(
        _snap(source_status_raw="", issue_date="", application_date=""),
        now=_utc(2026, 7, 2),
    )
    assert rule == "no_status_no_dates_unknown"


def test_recent_issue_without_source_status_stays_active():
    rule = evaluate_permit_lifecycle_transition(
        _snap(source_status_raw="", issue_date="2026-05-15", application_date=""),
        now=_utc(2026, 7, 2),
    )
    assert rule is None


def test_override_precedence_skips_transition():
    rule = evaluate_permit_lifecycle_transition(
        _snap(lifecycle_status_override="active", source_status_raw="Finaled"),
        now=_utc(2026, 7, 2),
    )
    assert rule is None


def test_apply_completed_sets_is_active_false():
    row = MagicMock()
    row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_ACTIVE
    row.is_active = True
    apply_permit_lifecycle_transition(row, "source_status_completed", now=_utc(2026, 7, 2))
    assert row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_COMPLETED
    assert row.is_active is False
    assert row.status_changed_at == _utc(2026, 7, 2)


def test_idempotent_stale_transition():
    snapshot = _snap(
        lifecycle_status=PERMIT_LIFECYCLE_STATUS_STALE,
        is_active=False,
        source_status_raw="",
        issue_date="2020-01-01",
    )
    assert evaluate_permit_lifecycle_transition(snapshot, now=_utc(2026, 7, 2)) is None


def test_resolve_permit_lifecycle_integration_mock():
    permit = MagicMock()
    permit.source = "vancouver"
    permit.lifecycle_status = PERMIT_LIFECYCLE_STATUS_ACTIVE
    permit.is_active = True
    permit.lifecycle_status_override = None
    permit.source_status_raw = "Finaled"
    permit.issue_date = "2026-01-01"
    permit.application_date = ""

    session = MagicMock()
    session.execute.return_value.all.return_value = [("vancouver",)]
    session.scalars.return_value.all.return_value = [permit]

    first = resolve_permit_lifecycle(session, now=_utc(2026, 7, 2), commit=False)
    second = resolve_permit_lifecycle(session, now=_utc(2026, 7, 2), commit=False)

    assert permit.lifecycle_status == PERMIT_LIFECYCLE_STATUS_COMPLETED
    assert permit.is_active is False
    assert first["totals"]["source_status_completed"] == 1
    assert second["totals"]["source_status_completed"] == 0
    assert second["totals"]["skipped_no_change"] == 1


def test_resolve_permit_lifecycle_endpoint_requires_internal_key():
    client = TestClient(app)
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        response = client.post("/internal/lifecycle/resolve-permits")
    assert response.status_code == 403


def test_resolve_permits_sync_returns_resolver_summary():
    client = TestClient(app)
    summary = {
        "resolved_at": "2026-07-02T00:00:00+00:00",
        "stale_age_days": 730,
        "cities": {},
        "totals": {"skipped_no_change": 111773},
    }
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch(
            "pipeline.permit_lifecycle_resolver.run_permit_lifecycle_resolve_job",
            return_value=summary,
        ) as mock_run:
            response = client.post(
                "/internal/lifecycle/resolve-permits",
                headers={"X-Internal-Key": "secret"},
            )
    assert response.status_code == 200
    assert response.json() == summary
    mock_run.assert_called_once()


def test_resolve_permits_background_returns_started_and_runs_job():
    client = TestClient(app)
    summary = {
        "resolved_at": "2026-07-02T00:00:00+00:00",
        "stale_age_days": 730,
        "cities": {},
        "totals": {"skipped_no_change": 111773},
    }
    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch(
            "pipeline.permit_lifecycle_resolver.run_permit_lifecycle_resolve_job",
            return_value=summary,
        ) as mock_run:
            response = client.post(
                "/internal/lifecycle/resolve-permits?background=true",
                headers={"X-Internal-Key": "secret"},
            )
    assert response.status_code == 200
    assert response.json() == {"status": "started"}
    mock_run.assert_called_once()
