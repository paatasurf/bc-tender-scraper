"""Unit tests for P2-02 tender lifecycle transitions."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from api import internal as internal_api
from db.lifecycle_constants import (
    LIFECYCLE_STATUS_ACTIVE,
    LIFECYCLE_STATUS_CLOSED,
    LIFECYCLE_STATUS_CLOSING_SOON,
    LIFECYCLE_STATUS_DELISTED,
)
from db.models import Tender
from db.tender_presence import sync_missing_from_source_counts, upsert_with_presence, TENDER_CONTENT_COLUMNS
from pipeline.lifecycle_resolver import (
    LifecycleRowSnapshot,
    apply_lifecycle_transition,
    evaluate_lifecycle_transition,
    has_manual_lifecycle_override,
    resolve_tender_lifecycle,
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _snapshot(**kwargs) -> LifecycleRowSnapshot:
    defaults = {
        "lifecycle_status": LIFECYCLE_STATUS_ACTIVE,
        "is_open": True,
        "lifecycle_status_override": None,
        "closing_at": None,
        "closed_at": None,
        "missing_from_source_count": 0,
    }
    defaults.update(kwargs)
    return LifecycleRowSnapshot(**defaults)


def test_closed_rule_when_closing_at_in_past():
    now = _utc(2026, 7, 10, 12)
    rule = evaluate_lifecycle_transition(
        _snapshot(closing_at=_utc(2026, 7, 1, 0)),
        now=now,
    )
    assert rule == "closed_past_closing_at"


def test_closing_soon_rule_within_seven_days_for_active():
    now = _utc(2026, 7, 10, 12)
    rule = evaluate_lifecycle_transition(
        _snapshot(
            lifecycle_status=LIFECYCLE_STATUS_ACTIVE,
            closing_at=_utc(2026, 7, 15, 0),
        ),
        now=now,
    )
    assert rule == "closing_soon_within_7_days"


def test_delisted_rule_when_missing_from_source_and_no_closing_at():
    rule = evaluate_lifecycle_transition(
        _snapshot(missing_from_source_count=3, closing_at=None),
        now=_utc(2026, 7, 10),
    )
    assert rule == "delisted_missing_from_source"


def test_override_precedence_skips_all_rules():
    now = _utc(2026, 7, 10)
    rule = evaluate_lifecycle_transition(
        _snapshot(
            lifecycle_status_override="closed",
            closing_at=_utc(2026, 1, 1),
            missing_from_source_count=99,
        ),
        now=now,
    )
    assert rule is None
    assert has_manual_lifecycle_override("closed")


def test_idempotent_closed_transition():
    now = _utc(2026, 7, 10)
    row = MagicMock()
    row.lifecycle_status = LIFECYCLE_STATUS_CLOSED
    row.is_open = False
    row.closed_at = _utc(2026, 7, 1)
    row.closing_at = _utc(2026, 7, 1)

    snapshot = LifecycleRowSnapshot(
        lifecycle_status=row.lifecycle_status,
        is_open=row.is_open,
        lifecycle_status_override=None,
        closing_at=row.closing_at,
        closed_at=row.closed_at,
        missing_from_source_count=0,
    )
    assert evaluate_lifecycle_transition(snapshot, now=now) is None


def test_idempotent_delisted_transition():
    snapshot = _snapshot(
        lifecycle_status=LIFECYCLE_STATUS_DELISTED,
        is_open=False,
        missing_from_source_count=5,
        closing_at=None,
    )
    assert evaluate_lifecycle_transition(snapshot, now=_utc(2026, 7, 10)) is None


def test_apply_closed_sets_closed_at_from_closing_at():
    row = MagicMock()
    row.closed_at = None
    closing = _utc(2026, 7, 1)
    row.closing_at = closing
    apply_lifecycle_transition(row, "closed_past_closing_at", now=_utc(2026, 7, 10))
    assert row.lifecycle_status == LIFECYCLE_STATUS_CLOSED
    assert row.is_open is False
    assert row.closed_at == closing


def test_closing_soon_does_not_apply_when_not_active():
    now = _utc(2026, 7, 10)
    rule = evaluate_lifecycle_transition(
        _snapshot(
            lifecycle_status=LIFECYCLE_STATUS_CLOSING_SOON,
            closing_at=_utc(2026, 7, 12),
        ),
        now=now,
    )
    assert rule is None


def test_delisted_does_not_apply_when_closing_at_present():
    rule = evaluate_lifecycle_transition(
        _snapshot(
            missing_from_source_count=10,
            closing_at=_utc(2026, 8, 1),
        ),
        now=_utc(2026, 7, 10),
    )
    assert rule is None


def test_resolve_lifecycle_endpoint_requires_internal_key():
    from unittest.mock import patch

    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with pytest.raises(Exception) as exc:
            internal_api.resolve_lifecycle(request)
    assert getattr(exc.value, "status_code", None) == 403


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI (set CI_DATABASE_URL to enable)")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing lifecycle DB integration tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def local_db_session() -> Session:
    from db.connection import init_db

    _require_local_database_url()
    init_db()
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _sample_row(url: str) -> dict:
    return {
        "title": "Lifecycle Test Tender",
        "organization": "Test Org",
        "category": "Construction",
        "posted_date": "2026-07-01",
        "closing_date": "2026-08-01",
        "estimated_value": "$1",
        "location": "Vancouver, BC",
        "tender_id": "LIFE-TEST",
        "url": url,
        "source": "test",
    }


def test_resolve_tender_lifecycle_integration_idempotent(local_db_session: Session):
    url = "https://lifecycle-p2-02.test/federal-closed"
    local_db_session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    local_db_session.commit()

    upsert_with_presence(
        local_db_session,
        Tender,
        [_sample_row(url)],
        "url",
        TENDER_CONTENT_COLUMNS,
    )
    row = local_db_session.scalar(select(Tender).where(Tender.url == url))
    assert row is not None
    row.closing_at = _utc(2026, 1, 1)
    row.lifecycle_status = LIFECYCLE_STATUS_ACTIVE
    row.is_open = True
    row.closed_at = None
    local_db_session.commit()

    before_closed = local_db_session.scalar(
        select(func.count()).select_from(Tender).where(Tender.lifecycle_status == LIFECYCLE_STATUS_CLOSED)
    )
    first = resolve_tender_lifecycle(local_db_session, now=_utc(2026, 7, 10))
    second = resolve_tender_lifecycle(local_db_session, now=_utc(2026, 7, 10))

    updated = local_db_session.scalar(select(Tender).where(Tender.url == url))
    assert updated is not None
    assert updated.lifecycle_status == LIFECYCLE_STATUS_CLOSED
    assert updated.is_open is False
    assert updated.closed_at is not None
    assert first["totals"]["closed_past_closing_at"] >= 1
    assert second["totals"]["closed_past_closing_at"] == 0

    after_closed = local_db_session.scalar(
        select(func.count()).select_from(Tender).where(Tender.lifecycle_status == LIFECYCLE_STATUS_CLOSED)
    )
    assert after_closed >= before_closed

    local_db_session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    local_db_session.commit()


def test_sync_missing_from_source_counts_integration(local_db_session: Session):
    url_present = "https://lifecycle-p2-02.test/present"
    url_absent = "https://lifecycle-p2-02.test/absent"
    local_db_session.execute(
        text("DELETE FROM tenders WHERE url IN (:a, :b)"),
        {"a": url_present, "b": url_absent},
    )
    local_db_session.commit()

    upsert_with_presence(
        local_db_session,
        Tender,
        [_sample_row(url_present), _sample_row(url_absent)],
        "url",
        TENDER_CONTENT_COLUMNS,
    )
    local_db_session.execute(
        text("UPDATE tenders SET missing_from_source_count = 2 WHERE url = :url"),
        {"url": url_absent},
    )
    local_db_session.commit()

    result = sync_missing_from_source_counts(local_db_session, Tender, {url_present})
    assert result["reset"] >= 1
    assert result["incremented"] >= 1

    present = local_db_session.scalar(select(Tender).where(Tender.url == url_present))
    absent = local_db_session.scalar(select(Tender).where(Tender.url == url_absent))
    assert present is not None and present.missing_from_source_count == 0
    assert absent is not None and absent.missing_from_source_count == 3

    local_db_session.execute(
        text("DELETE FROM tenders WHERE url IN (:a, :b)"),
        {"a": url_present, "b": url_absent},
    )
    local_db_session.commit()
