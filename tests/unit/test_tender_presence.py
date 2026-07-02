"""Unit tests for tender presence tracking (P1-02)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from db.connection import init_db
from db.import_csv import import_all_csvs
from db.models import ArchTender, CommercialTender, Tender
from db.tender_presence import (
    _stamp_presence_for_insert,
    upsert_with_presence,
    TENDER_CONTENT_COLUMNS,
)


def test_stamp_presence_for_insert_sets_all_three_timestamps():
    seen_at = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    row = _stamp_presence_for_insert({"url": "https://example.com/t/1"}, seen_at=seen_at)
    assert row["first_seen_at"] == seen_at
    assert row["last_seen_at"] == seen_at
    assert row["updated_at"] == seen_at


@pytest.fixture(scope="module")
def db_session() -> Session:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    init_db()
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _sample_federal(url: str, *, title: str = "Sample Federal Tender") -> dict:
    return {
        "title": title,
        "organization": "Test Org",
        "category": "Construction",
        "posted_date": "2026-07-01",
        "closing_date": "2026-08-01",
        "estimated_value": "$1",
        "location": "Vancouver, BC",
        "tender_id": "TEST-001",
        "url": url,
        "source": "test",
    }


def test_presence_upsert_preserves_first_seen_and_refreshes_last_seen(db_session: Session):
    url = "https://presence-test.example/federal-1"
    db_session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    db_session.commit()

    upsert_with_presence(
        db_session,
        Tender,
        [_sample_federal(url)],
        "url",
        TENDER_CONTENT_COLUMNS,
    )
    first = db_session.scalar(select(Tender).where(Tender.url == url))
    assert first is not None
    assert first.first_seen_at is not None
    assert first.last_seen_at is not None
    assert first.updated_at is not None
    first_seen = first.first_seen_at
    first_updated = first.updated_at
    first_last_seen = first.last_seen_at

    upsert_with_presence(
        db_session,
        Tender,
        [_sample_federal(url)],
        "url",
        TENDER_CONTENT_COLUMNS,
    )
    second = db_session.scalar(select(Tender).where(Tender.url == url))
    assert second is not None
    assert second.first_seen_at == first_seen
    assert second.last_seen_at >= first_last_seen
    assert second.updated_at == first_updated

    upsert_with_presence(
        db_session,
        Tender,
        [_sample_federal(url, title="Sample Federal Tender (revised)")],
        "url",
        TENDER_CONTENT_COLUMNS,
    )
    third = db_session.scalar(select(Tender).where(Tender.url == url))
    assert third is not None
    assert third.first_seen_at == first_seen
    assert third.updated_at > first_updated

    db_session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    db_session.commit()


def test_import_all_csvs_does_not_change_row_counts(db_session: Session):
    counts_before = {
        "tenders": db_session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": db_session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
        "arch_tenders": db_session.scalar(select(func.count()).select_from(ArchTender)) or 0,
    }
    import_all_csvs(db_session)
    counts_after_first = {
        "tenders": db_session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": db_session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
        "arch_tenders": db_session.scalar(select(func.count()).select_from(ArchTender)) or 0,
    }
    import_all_csvs(db_session)
    counts_after_second = {
        "tenders": db_session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": db_session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
        "arch_tenders": db_session.scalar(select(func.count()).select_from(ArchTender)) or 0,
    }
    assert counts_after_first == counts_after_second
    assert counts_after_first["tenders"] >= counts_before["tenders"]
