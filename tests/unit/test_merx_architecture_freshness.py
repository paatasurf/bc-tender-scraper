"""Tests for the MERX Architecture Ops Source Freshness fix (M3G).

FRESHNESS_SOURCES now reads ArchTender.last_seen_at instead of
ArchTender.scraped_at for "MERX Architecture" -- see
pipeline/ops_read_model.py's inline comment for the full explanation.

Real-DB integration tests only, mirroring
tests/unit/test_early_signal_import.py -- whether scraped_at/last_seen_at
are refreshed on a re-upsert is genuine PostgreSQL
ON CONFLICT DO UPDATE ... EXCLUDED semantics and cannot be meaningfully
verified against a mock. Skipped on CI and against any non-local
DATABASE_URL.

db/tender_presence.py itself is not touched by this fix -- these tests
prove the existing (unchanged) upsert behavior, then prove the read
model now surfaces it correctly.

Local Postgres in this environment may carry pre-existing committed
arch_tenders rows, so every test here uses a unique url (via _uid()) and
deletes only its own row before starting -- never a blanket DELETE.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

import pipeline.ops_read_model as rm
from db.models import ArchTender
from db.tender_presence import ARCH_CONTENT_COLUMNS, upsert_with_presence


def _uid() -> str:
    return uuid.uuid4().hex[:12]


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
            "Refusing MERX Architecture freshness integration tests against production DATABASE_URL"
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


def _row(url: str, **overrides) -> dict[str, str]:
    defaults = {
        "title": "Test Architecture RFPQ",
        "company": "",
        "value": "",
        "deadline": "",
        "status": "Open",
        "category": "",
        "url": url,
        "tender_id": "",
    }
    defaults.update(overrides)
    return defaults


def _upsert(session: Session, url: str, **overrides) -> None:
    upsert_with_presence(
        session, ArchTender, [_row(url, **overrides)], "url", ARCH_CONTENT_COLUMNS
    )


def _cleanup(session: Session, url: str) -> None:
    session.execute(text("DELETE FROM arch_tenders WHERE url = :url"), {"url": url})
    session.commit()


def _fetch(session: Session, url: str) -> ArchTender:
    row = session.scalars(select(ArchTender).where(ArchTender.url == url)).one()
    session.expunge(row)
    return row


def test_new_arch_tender_gets_scraped_at_and_last_seen_at(local_db_session: Session):
    """A fresh INSERT gets non-null scraped_at and last_seen_at."""
    url = f"https://example.test/arch/{_uid()}"
    _cleanup(local_db_session, url)
    try:
        _upsert(local_db_session, url)
        record = _fetch(local_db_session, url)
        assert record.scraped_at is not None
        assert record.last_seen_at is not None
    finally:
        _cleanup(local_db_session, url)


def test_repeated_upsert_leaves_scraped_at_unchanged(local_db_session: Session):
    """Proof 1: re-upserting the same url (existing row) does NOT advance
    scraped_at -- db/tender_presence.py is untouched by this fix, this is
    the existing, unchanged upsert_with_presence() behavior."""
    url = f"https://example.test/arch/{_uid()}"
    _cleanup(local_db_session, url)
    try:
        _upsert(local_db_session, url)
        first = _fetch(local_db_session, url)

        time.sleep(0.05)
        _upsert(local_db_session, url, status="Still Open")
        second = _fetch(local_db_session, url)

        assert second.scraped_at == first.scraped_at
    finally:
        _cleanup(local_db_session, url)


def test_repeated_upsert_advances_last_seen_at(local_db_session: Session):
    """Proof 2: re-upserting the same url DOES advance last_seen_at --
    this is the field the fixed FreshnessSource now reads."""
    url = f"https://example.test/arch/{_uid()}"
    _cleanup(local_db_session, url)
    try:
        _upsert(local_db_session, url)
        first = _fetch(local_db_session, url)

        time.sleep(0.05)
        _upsert(local_db_session, url, status="Still Open")
        second = _fetch(local_db_session, url)

        assert second.last_seen_at > first.last_seen_at
    finally:
        _cleanup(local_db_session, url)


def test_ops_freshness_healthy_via_last_seen_at_after_repeat_upsert(
    local_db_session: Session,
):
    """Proof 3: reproduces the exact production symptom and proves the
    fix. A row is backdated to simulate a tender last freshly inserted
    30 hours ago (past the 24h healthy threshold, matching the real
    MERX Architecture symptom of ~28h stale scraped_at) with no content
    change since. It is then repeat-upserted "today" -- exactly what
    happens to a stable "Open & Ongoing" tender that only ever hits the
    UPDATE branch. Reading Ops Source Freshness via last_seen_at (the
    fixed FreshnessSource) reports healthy; reading the same row via the
    old scraped_at field still shows degraded/stale, confirming this is
    genuinely last_seen_at fixing it and not some other change."""
    url = f"https://example.test/arch/{_uid()}"
    _cleanup(local_db_session, url)
    try:
        _upsert(local_db_session, url)
        local_db_session.execute(
            text(
                "UPDATE arch_tenders SET scraped_at = NOW() - INTERVAL '30 hours' "
                "WHERE url = :url"
            ),
            {"url": url},
        )
        local_db_session.commit()

        _upsert(local_db_session, url)  # identical content, repeat upsert

        fixed_source = rm.FreshnessSource(
            "MERX Architecture (test)", ArchTender, "last_seen_at", "url", url
        )
        fixed_result = rm.compute_source_freshness(local_db_session, fixed_source)
        assert fixed_result["status"] == "healthy"
        assert fixed_result["freshness_hours"] < 1.0

        stale_source = rm.FreshnessSource(
            "MERX Architecture (scraped_at, pre-fix)",
            ArchTender,
            "scraped_at",
            "url",
            url,
        )
        stale_result = rm.compute_source_freshness(local_db_session, stale_source)
        assert stale_result["status"] in ("degraded", "stale")
    finally:
        _cleanup(local_db_session, url)


def test_production_freshness_source_uses_last_seen_at_for_merx_architecture():
    """Guard against silent regression: the actual FRESHNESS_SOURCES
    tuple used by GET /api/ops/sources must be wired to last_seen_at for
    MERX Architecture, not scraped_at. Pure/no DB -- always runs."""
    entry = next(s for s in rm.FRESHNESS_SOURCES if s.name == "MERX Architecture")
    assert entry.timestamp_column == "last_seen_at"
    assert entry.model is ArchTender
