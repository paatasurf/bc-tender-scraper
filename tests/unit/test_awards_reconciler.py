"""Unit tests for P2-03 awards reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from db.lifecycle_constants import LIFECYCLE_STATUS_AWARDED, LIFECYCLE_STATUS_CLOSED
from pipeline.awards_reconciler import (
    AWARD_MATCH_CONFIDENCE_HIGH,
    MATCH_SIGNAL_EZ899_CODE,
    MATCH_SIGNAL_TITLE_BUYER,
    AwardIndexes,
    build_award_indexes,
    extract_solicitation_codes,
    find_award_match,
    normalize_match_text,
    reconcile_awards,
)
from pipeline.lifecycle_resolver import has_manual_lifecycle_override


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _award(
    *,
    award_id: int,
    title: str,
    buyer: str = "Department of National Defence",
    award_date: str = "2025-09-19",
    description: str = "",
) -> MagicMock:
    award = MagicMock()
    award.id = award_id
    award.title = title
    award.buyer_organization = buyer
    award.award_date = award_date
    award.description = description
    return award


def test_extract_solicitation_codes_from_title():
    codes = extract_solicitation_codes("ITQ EZ899-270265 Penticton Airport Fire Pump Replacement")
    assert codes == {"EZ899-270265"}


def test_normalize_match_text_strips_punctuation_and_case():
    assert normalize_match_text("PUMP UNIT, ROTARY") == "pump unit rotary"
    assert normalize_match_text("  Cable,  Special Purpose ") == "cable special purpose"


def test_find_award_match_by_ez899_code():
    indexes = build_award_indexes(
        [
            _award(
                award_id=42,
                title="EZ899-270265 Penticton Airport Fire Pump Replacement",
                buyer="Department of Public Works and Government Services (PSPC)",
                award_date="2026-07-15",
            )
        ]
    )
    match = find_award_match(
        title="ITQ EZ899-270265 Penticton Airport Fire Pump Replacement",
        buyer="Department of Public Works and Government Services (PSPC)",
        closed_at=_utc(2026, 7, 2),
        indexes=indexes,
        now=_utc(2026, 7, 10),
    )
    assert match is not None
    assert match.award_id == 42
    assert match.signal == MATCH_SIGNAL_EZ899_CODE


def test_find_award_match_by_exact_title_and_buyer():
    indexes = build_award_indexes([_award(award_id=955, title="PUMP UNIT, ROTARY")])
    match = find_award_match(
        title="PUMP UNIT, ROTARY",
        buyer="Department of National Defence",
        closed_at=_utc(2026, 6, 12),
        indexes=indexes,
        now=_utc(2026, 7, 10),
    )
    assert match is not None
    assert match.award_id == 955
    assert match.signal == MATCH_SIGNAL_TITLE_BUYER


def test_find_award_match_returns_none_when_no_candidate():
    indexes = AwardIndexes(by_code={}, by_title_buyer={})
    match = find_award_match(
        title="Unrelated municipal sidewalk project",
        buyer="City of Vancouver",
        closed_at=_utc(2026, 7, 1),
        indexes=indexes,
        now=_utc(2026, 7, 10),
    )
    assert match is None


def test_find_award_match_skips_ambiguous_code_matches():
    indexes = build_award_indexes(
        [
            _award(award_id=1, title="EZ899-240846 Workspaces Furniture A", award_date="2024-01-01"),
            _award(award_id=2, title="EZ899-240846 Workspaces Furniture B", award_date="2024-02-01"),
        ]
    )
    match = find_award_match(
        title="EZ899-240846 Workspaces Furniture",
        buyer="PSPC",
        closed_at=_utc(2026, 7, 1),
        indexes=indexes,
        now=_utc(2026, 7, 10),
    )
    assert match is None


def test_reconcile_awards_marks_closed_tender_on_title_buyer_match():
    tender = MagicMock()
    tender.lifecycle_status = LIFECYCLE_STATUS_CLOSED
    tender.lifecycle_status_override = None
    tender.title = "PUMP UNIT, ROTARY"
    tender.organization = "Department of National Defence"
    tender.closed_at = _utc(2026, 6, 12)
    tender.closing_at = _utc(2026, 6, 12)
    tender.award_id = None

    session = MagicMock()
    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[_award(award_id=955, title="PUMP UNIT, ROTARY")])),
        MagicMock(all=MagicMock(return_value=[tender])),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    result = reconcile_awards(session, now=_utc(2026, 7, 10), commit=False)

    assert tender.lifecycle_status == LIFECYCLE_STATUS_AWARDED
    assert tender.is_open is False
    assert tender.award_id == 955
    assert tender.award_match_confidence == AWARD_MATCH_CONFIDENCE_HIGH
    assert tender.awarded_at == _utc(2025, 9, 19)
    assert result["totals"][MATCH_SIGNAL_TITLE_BUYER] == 1
    session.commit.assert_not_called()


def test_reconcile_awards_leaves_unmatched_closed_tender():
    tender = MagicMock()
    tender.lifecycle_status = LIFECYCLE_STATUS_CLOSED
    tender.lifecycle_status_override = None
    tender.title = "New Sidewalks Salmo Parks"
    tender.organization = "Village of Salmo"
    tender.closed_at = _utc(2026, 7, 1)
    tender.closing_at = _utc(2026, 7, 1)
    tender.award_id = None

    session = MagicMock()
    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[tender])),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    result = reconcile_awards(session, now=_utc(2026, 7, 10), commit=False)

    assert tender.lifecycle_status == LIFECYCLE_STATUS_CLOSED
    assert tender.award_id is None
    assert result["totals"]["skipped_no_match"] == 1


def test_reconcile_awards_skips_override():
    tender = MagicMock()
    tender.lifecycle_status = LIFECYCLE_STATUS_CLOSED
    tender.lifecycle_status_override = "closed"
    tender.title = "PUMP UNIT, ROTARY"
    tender.organization = "Department of National Defence"

    session = MagicMock()
    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[_award(award_id=955, title="PUMP UNIT, ROTARY")])),
        MagicMock(all=MagicMock(return_value=[tender])),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    result = reconcile_awards(session, now=_utc(2026, 7, 10), commit=False)

    assert tender.lifecycle_status == LIFECYCLE_STATUS_CLOSED
    assert result["totals"]["skipped_override"] == 1
    assert has_manual_lifecycle_override("closed")


def test_reconcile_awards_idempotent_when_already_awarded():
    tender = MagicMock()
    tender.lifecycle_status = LIFECYCLE_STATUS_AWARDED
    tender.lifecycle_status_override = None
    tender.award_id = 955

    side_effect = [
        MagicMock(all=MagicMock(return_value=[_award(award_id=955, title="PUMP UNIT, ROTARY")])),
        MagicMock(all=MagicMock(return_value=[tender])),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]
    session = MagicMock()
    session.scalars.side_effect = side_effect * 2

    first = reconcile_awards(session, now=_utc(2026, 7, 10), commit=False)
    second = reconcile_awards(session, now=_utc(2026, 7, 10), commit=False)

    assert first["totals"]["skipped_already_awarded"] == 1
    assert second["totals"]["skipped_already_awarded"] == 1
    assert first["totals"][MATCH_SIGNAL_TITLE_BUYER] == 0


def test_reconcile_awards_endpoint_requires_internal_key():
    client = TestClient(app)

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        response = client.post("/internal/lifecycle/reconcile-awards")

    assert response.status_code == 403
