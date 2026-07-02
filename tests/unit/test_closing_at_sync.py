"""Unit tests for closing_at import/backfill sync helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from api import internal as internal_api
from db.closing_at_sync import sync_closing_at_from_deadline
from db.models import Tender
from db.tender_presence import TENDER_CONTENT_COLUMNS, upsert_with_presence


def _require_local_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing closing_at sync integration tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def local_db_session() -> Session:
    import config.env  # noqa: F401
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


def test_sync_closing_at_from_deadline_integration(local_db_session: Session):
    url = "https://closing-at-p2-06.test/federal"
    local_db_session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    local_db_session.commit()

    upsert_with_presence(
        local_db_session,
        Tender,
        [
            {
                "title": "Closing At Test",
                "organization": "Org",
                "category": "Construction",
                "posted_date": "2026-07-01",
                "closing_date": "2026/08/15",
                "estimated_value": "$1",
                "location": "BC",
                "tender_id": "CAT-1",
                "url": url,
                "source": "test",
            }
        ],
        "url",
        TENDER_CONTENT_COLUMNS,
    )
    result = sync_closing_at_from_deadline(
        local_db_session,
        Tender,
        "closing_date",
        urls={url},
    )
    row = local_db_session.scalar(select(Tender).where(Tender.url == url))
    assert row is not None
    assert result["updated"] == 1
    assert row.closing_at is not None
    assert row.closing_at.hour == 23
    assert row.closing_at.minute == 59

    local_db_session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    local_db_session.commit()


def test_sync_skips_unparseable_without_clearing_existing():
    session = MagicMock()
    row = MagicMock()
    row.closing_at = object()
    row.closing_date = "Not Available"
    session.scalars.return_value = iter([row])

    result = sync_closing_at_from_deadline(session, Tender, "closing_date", urls={"x"})

    assert result["updated"] == 0
    assert result["skipped_unparseable"] == 1
    session.commit.assert_not_called()


def test_backfill_closing_at_endpoint_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with pytest.raises(Exception) as exc:
            internal_api.backfill_closing_at(request)
    assert getattr(exc.value, "status_code", None) == 403
