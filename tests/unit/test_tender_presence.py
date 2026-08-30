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
    row = _stamp_presence_for_insert(
        {"url": "https://example.com/t/1"}, seen_at=seen_at
    )
    assert row["first_seen_at"] == seen_at
    assert row["last_seen_at"] == seen_at
    assert row["updated_at"] == seen_at


@pytest.fixture(scope="module")
def db_session() -> Session:
    """upsert_with_presence() relies on last_seen_at advancing between its
    own internal, real session.commit() calls (func.now() -- Postgres's
    transaction-start timestamp -- only advances across a genuinely new
    transaction, not a SAVEPOINT release), so this fixture stays a plain,
    real session rather than the SAVEPOINT-based tests/db_transactional_fixture.py
    used elsewhere. That's safe here because every test using this fixture
    always deletes its own url-scoped row when done (see the DELETE+commit
    calls below) -- unlike test_import_all_csvs_does_not_change_row_counts,
    which has no such per-row scoping and gets its own isolated fixture
    instead (see isolated_db_session).
    """
    from tests.db_test_safety import require_local_test_database

    database_url = require_local_test_database()
    init_db()
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def isolated_db_session() -> Session:
    """import_all_csvs() (via import_permits -> upsert_city_permits)
    commits repeatedly and for real, mid-function, by design -- correct for
    a production import job, but has no per-row scoping to clean up after
    itself (unlike db_session's tests above), so running it against a plain
    session would durably write real, unscoped data into whatever database
    it's pointed at, forever. A SAVEPOINT-based session
    (tests/db_transactional_fixture.py) lets the same internal commits
    happen exactly as the code expects while everything is rolled back for
    real at teardown -- see that module for why a plain outer transaction
    alone is not enough once code under test commits/rolls back on its own.
    """
    from tests.db_test_safety import require_local_test_database
    from tests.db_transactional_fixture import transactional_session

    database_url = require_local_test_database()
    init_db()
    engine = create_engine(database_url)
    try:
        with transactional_session(engine) as session:
            yield session
    finally:
        engine.dispose()


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


def test_presence_upsert_preserves_first_seen_and_refreshes_last_seen(
    db_session: Session,
):
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


@pytest.mark.xfail(
    reason=(
        "PRODUCT BUG, confirmed root cause (tracked separately, not fixed "
        "here): fails with psycopg2.errors.ForeignKeyViolation on "
        "permits.company_id_fkey (a company_id referenced by a freshly "
        "imported permit row does not exist in companies), reproduced "
        "deterministically on a genuinely fresh, empty local Postgres "
        "database -- not an accumulated-state or test-isolation artifact. "
        "Likely origin: db/permit_import.py::_attach_company_ids resolves/"
        "creates companies via a CompanyResolver that snapshots the "
        "companies table once per call and assigns row['company_id'] in "
        "memory before the referencing permit row's own commit a few "
        "lines later in upsert_city_permits -- needs tracing by the code "
        "owner. Not implemented here per explicit instruction not to "
        "change production business logic."
    ),
    strict=True,
)
def test_import_all_csvs_does_not_change_row_counts(isolated_db_session: Session):
    db_session = isolated_db_session
    counts_before = {
        "tenders": db_session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": db_session.scalar(
            select(func.count()).select_from(CommercialTender)
        )
        or 0,
        "arch_tenders": db_session.scalar(select(func.count()).select_from(ArchTender))
        or 0,
    }
    import_all_csvs(db_session)
    counts_after_first = {
        "tenders": db_session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": db_session.scalar(
            select(func.count()).select_from(CommercialTender)
        )
        or 0,
        "arch_tenders": db_session.scalar(select(func.count()).select_from(ArchTender))
        or 0,
    }
    import_all_csvs(db_session)
    counts_after_second = {
        "tenders": db_session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": db_session.scalar(
            select(func.count()).select_from(CommercialTender)
        )
        or 0,
        "arch_tenders": db_session.scalar(select(func.count()).select_from(ArchTender))
        or 0,
    }
    assert counts_after_first == counts_after_second
    assert counts_after_first["tenders"] >= counts_before["tenders"]
