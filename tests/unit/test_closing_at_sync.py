"""Unit tests for closing_at import/backfill sync helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from api import internal as internal_api
from db.closing_at_parser import VANCOUVER_TZ
from db.closing_at_sync import sync_closing_at_from_deadline
from db.models import Tender
from db.tender_presence import TENDER_CONTENT_COLUMNS, upsert_with_presence


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip(
            "DB integration tests skipped on CI (set CI_DATABASE_URL to enable)"
        )
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip(
            "Refusing closing_at sync integration tests against production DATABASE_URL"
        )
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
    # db/closing_at_parser.py's documented contract is "23:59:59 local
    # America/Vancouver time" -- after a round-trip through a TIMESTAMPTZ
    # column, psycopg2/SQLAlchemy return the value normalized to whatever
    # timezone the connecting session happens to use (UTC on CI's Postgres
    # container), so .hour/.minute on the raw value is environment-
    # dependent. Convert explicitly to the parser's own timezone before
    # asserting its documented contract, rather than asserting on however
    # the current session happens to be configured.
    closing_at_vancouver = row.closing_at.astimezone(VANCOUVER_TZ)
    assert closing_at_vancouver.hour == 23
    assert closing_at_vancouver.minute == 59

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
