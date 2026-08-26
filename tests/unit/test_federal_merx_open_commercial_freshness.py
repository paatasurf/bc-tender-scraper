"""Tests for the Federal / MERX Open / Commercial Ops Source Freshness fix
(M3H follow-up to PR #135's MERX Architecture fix).

FRESHNESS_SOURCES now reads Tender.last_seen_at (for both the "Federal"
and "MERX Open" entries, which share the tenders table, distinguished by
source) and CommercialTender.last_seen_at instead of scraped_at -- see
pipeline/ops_read_model.py's inline comment for the full explanation.

Real-DB integration tests only, mirroring
tests/unit/test_merx_architecture_freshness.py exactly -- whether
scraped_at/last_seen_at are refreshed on a re-upsert is genuine
PostgreSQL ON CONFLICT DO UPDATE ... EXCLUDED semantics and cannot be
meaningfully verified against a mock. Skipped on CI and against any
non-local DATABASE_URL.

db/tender_presence.py itself is not touched by this fix -- these tests
prove the existing (unchanged) upsert behavior, then prove the read
model now surfaces it correctly, for all three affected sources.

Local Postgres in this environment may carry pre-existing committed
tenders/commercial_tenders rows, so every test here uses a unique url
(via _uid()) and deletes only its own row before starting -- never a
blanket DELETE.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

import pipeline.ops_read_model as rm
from db.import_csv import AI_PRESERVE_COLUMNS
from db.models import CommercialTender, Tender
from db.tender_presence import (
    COMMERCIAL_CONTENT_COLUMNS,
    TENDER_CONTENT_COLUMNS,
    upsert_with_presence,
)

_FEDERAL_SOURCE = "buyandsell.gc.ca"
_MERX_OPEN_SOURCE = "merx.com"


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
            "Refusing Federal/MERX Open/Commercial freshness integration tests "
            "against production DATABASE_URL"
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


# --- Tender (Federal / MERX Open) helpers ----------------------------------


def _tender_row(url: str, source: str, **overrides) -> dict[str, str]:
    defaults = {
        "title": "Test Tender",
        "organization": "",
        "category": "",
        "posted_date": "",
        "closing_date": "",
        "estimated_value": "",
        "location": "",
        "tender_id": "",
        "url": url,
        "source": source,
    }
    defaults.update(overrides)
    return defaults


def _upsert_tender(session: Session, url: str, source: str, **overrides) -> None:
    upsert_with_presence(
        session,
        Tender,
        [_tender_row(url, source, **overrides)],
        "url",
        TENDER_CONTENT_COLUMNS,
        preserve_on_update=AI_PRESERVE_COLUMNS,
    )


def _cleanup_tender(session: Session, url: str) -> None:
    session.execute(text("DELETE FROM tenders WHERE url = :url"), {"url": url})
    session.commit()


def _fetch_tender(session: Session, url: str) -> Tender:
    row = session.scalars(select(Tender).where(Tender.url == url)).one()
    session.expunge(row)
    return row


# --- CommercialTender helpers ------------------------------------------------


def _commercial_row(url: str, **overrides) -> dict[str, str]:
    defaults = {
        "title": "Test Commercial Tender",
        "company": "",
        "value": "",
        "deadline": "",
        "status": "Open",
        "category": "",
        "url": url,
        "tender_id": "",
        "source": "",
    }
    defaults.update(overrides)
    return defaults


def _upsert_commercial(session: Session, url: str, **overrides) -> None:
    upsert_with_presence(
        session,
        CommercialTender,
        [_commercial_row(url, **overrides)],
        "url",
        COMMERCIAL_CONTENT_COLUMNS,
        preserve_on_update=AI_PRESERVE_COLUMNS,
    )


def _cleanup_commercial(session: Session, url: str) -> None:
    session.execute(
        text("DELETE FROM commercial_tenders WHERE url = :url"), {"url": url}
    )
    session.commit()


def _fetch_commercial(session: Session, url: str) -> CommercialTender:
    row = session.scalars(
        select(CommercialTender).where(CommercialTender.url == url)
    ).one()
    session.expunge(row)
    return row


# =============================================================================
# Federal (Tender, source="buyandsell.gc.ca")
# =============================================================================


def test_federal_new_row_gets_scraped_at_and_last_seen_at(local_db_session: Session):
    url = f"https://example.test/federal/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE)
        record = _fetch_tender(local_db_session, url)
        assert record.scraped_at is not None
        assert record.last_seen_at is not None
    finally:
        _cleanup_tender(local_db_session, url)


def test_federal_repeated_upsert_leaves_scraped_at_unchanged(
    local_db_session: Session,
):
    """Proof (b): db/tender_presence.py is untouched by this fix -- a
    repeat upsert of an existing Federal row does NOT advance
    scraped_at."""
    url = f"https://example.test/federal/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE)
        first = _fetch_tender(local_db_session, url)

        time.sleep(0.05)
        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE, category="Updated")
        second = _fetch_tender(local_db_session, url)

        assert second.scraped_at == first.scraped_at
    finally:
        _cleanup_tender(local_db_session, url)


def test_federal_repeated_upsert_advances_last_seen_at(local_db_session: Session):
    """Proof (c): last_seen_at DOES advance on repeat upsert -- the field
    the fixed FreshnessSource now reads."""
    url = f"https://example.test/federal/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE)
        first = _fetch_tender(local_db_session, url)

        time.sleep(0.05)
        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE, category="Updated")
        second = _fetch_tender(local_db_session, url)

        assert second.last_seen_at > first.last_seen_at
    finally:
        _cleanup_tender(local_db_session, url)


def test_federal_ops_freshness_healthy_via_last_seen_at_after_repeat_upsert(
    local_db_session: Session,
):
    """Proof (d): reproduces the exact production symptom -- a Federal
    row backdated 30h (unchanged content since), repeat-upserted "today",
    reads healthy via last_seen_at while the old scraped_at field still
    reads degraded/stale for the same row."""
    url = f"https://example.test/federal/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE)
        local_db_session.execute(
            text(
                "UPDATE tenders SET scraped_at = NOW() - INTERVAL '30 hours' "
                "WHERE url = :url"
            ),
            {"url": url},
        )
        local_db_session.commit()

        _upsert_tender(local_db_session, url, _FEDERAL_SOURCE)  # identical content

        fixed_source = rm.FreshnessSource(
            "Federal (test)", Tender, "last_seen_at", "url", url
        )
        fixed_result = rm.compute_source_freshness(local_db_session, fixed_source)
        assert fixed_result["status"] == "healthy"
        assert fixed_result["freshness_hours"] < 1.0

        stale_source = rm.FreshnessSource(
            "Federal (scraped_at, pre-fix)", Tender, "scraped_at", "url", url
        )
        stale_result = rm.compute_source_freshness(local_db_session, stale_source)
        assert stale_result["status"] in ("degraded", "stale")
    finally:
        _cleanup_tender(local_db_session, url)


# =============================================================================
# MERX Open (Tender, source="merx.com")
# =============================================================================


def test_merx_open_new_row_gets_scraped_at_and_last_seen_at(
    local_db_session: Session,
):
    url = f"https://example.test/merx-open/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE)
        record = _fetch_tender(local_db_session, url)
        assert record.scraped_at is not None
        assert record.last_seen_at is not None
    finally:
        _cleanup_tender(local_db_session, url)


def test_merx_open_repeated_upsert_leaves_scraped_at_unchanged(
    local_db_session: Session,
):
    url = f"https://example.test/merx-open/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE)
        first = _fetch_tender(local_db_session, url)

        time.sleep(0.05)
        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE, category="Updated")
        second = _fetch_tender(local_db_session, url)

        assert second.scraped_at == first.scraped_at
    finally:
        _cleanup_tender(local_db_session, url)


def test_merx_open_repeated_upsert_advances_last_seen_at(local_db_session: Session):
    url = f"https://example.test/merx-open/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE)
        first = _fetch_tender(local_db_session, url)

        time.sleep(0.05)
        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE, category="Updated")
        second = _fetch_tender(local_db_session, url)

        assert second.last_seen_at > first.last_seen_at
    finally:
        _cleanup_tender(local_db_session, url)


def test_merx_open_ops_freshness_healthy_via_last_seen_at_after_repeat_upsert(
    local_db_session: Session,
):
    url = f"https://example.test/merx-open/{_uid()}"
    _cleanup_tender(local_db_session, url)
    try:
        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE)
        local_db_session.execute(
            text(
                "UPDATE tenders SET scraped_at = NOW() - INTERVAL '30 hours' "
                "WHERE url = :url"
            ),
            {"url": url},
        )
        local_db_session.commit()

        _upsert_tender(local_db_session, url, _MERX_OPEN_SOURCE)

        fixed_source = rm.FreshnessSource(
            "MERX Open (test)", Tender, "last_seen_at", "url", url
        )
        fixed_result = rm.compute_source_freshness(local_db_session, fixed_source)
        assert fixed_result["status"] == "healthy"
        assert fixed_result["freshness_hours"] < 1.0

        stale_source = rm.FreshnessSource(
            "MERX Open (scraped_at, pre-fix)", Tender, "scraped_at", "url", url
        )
        stale_result = rm.compute_source_freshness(local_db_session, stale_source)
        assert stale_result["status"] in ("degraded", "stale")
    finally:
        _cleanup_tender(local_db_session, url)


# =============================================================================
# Commercial (CommercialTender)
# =============================================================================


def test_commercial_new_row_gets_scraped_at_and_last_seen_at(
    local_db_session: Session,
):
    url = f"https://example.test/commercial/{_uid()}"
    _cleanup_commercial(local_db_session, url)
    try:
        _upsert_commercial(local_db_session, url)
        record = _fetch_commercial(local_db_session, url)
        assert record.scraped_at is not None
        assert record.last_seen_at is not None
    finally:
        _cleanup_commercial(local_db_session, url)


def test_commercial_repeated_upsert_leaves_scraped_at_unchanged(
    local_db_session: Session,
):
    url = f"https://example.test/commercial/{_uid()}"
    _cleanup_commercial(local_db_session, url)
    try:
        _upsert_commercial(local_db_session, url)
        first = _fetch_commercial(local_db_session, url)

        time.sleep(0.05)
        _upsert_commercial(local_db_session, url, status="Still Open")
        second = _fetch_commercial(local_db_session, url)

        assert second.scraped_at == first.scraped_at
    finally:
        _cleanup_commercial(local_db_session, url)


def test_commercial_repeated_upsert_advances_last_seen_at(local_db_session: Session):
    url = f"https://example.test/commercial/{_uid()}"
    _cleanup_commercial(local_db_session, url)
    try:
        _upsert_commercial(local_db_session, url)
        first = _fetch_commercial(local_db_session, url)

        time.sleep(0.05)
        _upsert_commercial(local_db_session, url, status="Still Open")
        second = _fetch_commercial(local_db_session, url)

        assert second.last_seen_at > first.last_seen_at
    finally:
        _cleanup_commercial(local_db_session, url)


def test_commercial_ops_freshness_healthy_via_last_seen_at_after_repeat_upsert(
    local_db_session: Session,
):
    url = f"https://example.test/commercial/{_uid()}"
    _cleanup_commercial(local_db_session, url)
    try:
        _upsert_commercial(local_db_session, url)
        local_db_session.execute(
            text(
                "UPDATE commercial_tenders SET scraped_at = NOW() - INTERVAL '30 hours' "
                "WHERE url = :url"
            ),
            {"url": url},
        )
        local_db_session.commit()

        _upsert_commercial(local_db_session, url)

        fixed_source = rm.FreshnessSource(
            "Commercial (test)", CommercialTender, "last_seen_at", "url", url
        )
        fixed_result = rm.compute_source_freshness(local_db_session, fixed_source)
        assert fixed_result["status"] == "healthy"
        assert fixed_result["freshness_hours"] < 1.0

        stale_source = rm.FreshnessSource(
            "Commercial (scraped_at, pre-fix)",
            CommercialTender,
            "scraped_at",
            "url",
            url,
        )
        stale_result = rm.compute_source_freshness(local_db_session, stale_source)
        assert stale_result["status"] in ("degraded", "stale")
    finally:
        _cleanup_commercial(local_db_session, url)


# =============================================================================
# Proof (e): production FRESHNESS_SOURCES wiring -- pure, no DB, always runs
# =============================================================================


def test_production_freshness_sources_use_last_seen_at_for_all_three():
    """Guard against silent regression: the actual FRESHNESS_SOURCES tuple
    used by GET /api/ops/sources must be wired to last_seen_at for
    Federal, MERX Open, and Commercial -- not scraped_at."""
    by_name = {s.name: s for s in rm.FRESHNESS_SOURCES}

    federal = by_name["Federal"]
    assert federal.timestamp_column == "last_seen_at"
    assert federal.model is Tender
    assert federal.filter_column == "source"
    assert federal.filter_value == _FEDERAL_SOURCE

    merx_open = by_name["MERX Open"]
    assert merx_open.timestamp_column == "last_seen_at"
    assert merx_open.model is Tender
    assert merx_open.filter_column == "source"
    assert merx_open.filter_value == _MERX_OPEN_SOURCE

    commercial = by_name["Commercial"]
    assert commercial.timestamp_column == "last_seen_at"
    assert commercial.model is CommercialTender
