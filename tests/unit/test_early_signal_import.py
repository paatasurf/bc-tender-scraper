"""Unit tests for db/early_signal_import.py's upsert_early_signal_events().

Real-DB integration tests only -- the behavior under test (whether
scraped_at is refreshed on a re-upsert, and whether address/applicant/
project_value are protected from overwrite) is genuinely PostgreSQL
ON CONFLICT DO UPDATE ... EXCLUDED semantics; it cannot be meaningfully
verified against a mock. Skipped on CI and against any non-local
DATABASE_URL, matching every other local_db_session-based test file in
this repo (e.g. tests/unit/test_closing_at_sync.py).

Local Postgres in this environment may carry pre-existing committed rows
from earlier sessions, so every test here uses a unique (source,
external_id) pair (via _uid()) and deletes only its own rows before
starting -- never a blanket DELETE.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from db.early_signal_import import upsert_early_signal_events
from db.models import EarlySignalEvent

_TEST_SOURCE = "early_signal_freshness_fix_test"


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
            "Refusing early signal import integration tests against production DATABASE_URL"
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


def _row(external_id: str, **overrides) -> dict[str, str]:
    defaults = {
        "external_id": external_id,
        "source": _TEST_SOURCE,
        "transaction_date": "2026-01-01",
        "municipality": "Vancouver",
        "region": "Downtown",
        "property_type": "Mixed-use",
        "signal_type": "rezoning_application",
        "url_link": "https://example.test/project/1",
        "address": "123 Original St",
        "applicant": "Original Applicant Ltd",
        "project_value": "1000000",
    }
    defaults.update(overrides)
    return defaults


def _cleanup(session: Session, external_id: str) -> None:
    session.execute(
        text(
            "DELETE FROM early_signal_events WHERE source = :source "
            "AND external_id = :external_id"
        ),
        {"source": _TEST_SOURCE, "external_id": external_id},
    )
    session.commit()


def _fetch(session: Session, external_id: str) -> EarlySignalEvent:
    row = session.scalars(
        select(EarlySignalEvent).where(
            EarlySignalEvent.source == _TEST_SOURCE,
            EarlySignalEvent.external_id == external_id,
        )
    ).one()
    session.expunge(row)
    return row


def test_new_record_gets_scraped_at(local_db_session: Session):
    """Proof 1: a fresh INSERT (no prior conflicting row) gets a
    non-null scraped_at."""
    external_id = _uid()
    _cleanup(local_db_session, external_id)
    try:
        upsert_early_signal_events(local_db_session, [_row(external_id)])

        record = _fetch(local_db_session, external_id)
        assert record.scraped_at is not None
    finally:
        _cleanup(local_db_session, external_id)


def test_repeated_upsert_updates_scraped_at(local_db_session: Session):
    """Proof 2: re-upserting an existing (source, external_id) row
    advances scraped_at -- this is the actual freshness-bug fix."""
    external_id = _uid()
    _cleanup(local_db_session, external_id)
    try:
        upsert_early_signal_events(local_db_session, [_row(external_id)])
        first = _fetch(local_db_session, external_id)
        first_scraped_at = first.scraped_at

        time.sleep(0.05)  # guarantee a measurably later now() on re-upsert

        upsert_early_signal_events(
            local_db_session, [_row(external_id, region="Downtown Updated")]
        )
        second = _fetch(local_db_session, external_id)

        assert second.scraped_at > first_scraped_at
    finally:
        _cleanup(local_db_session, external_id)


def test_protected_fields_not_overwritten_on_repeat_upsert(local_db_session: Session):
    """Proof 3: address, applicant, and project_value must still be
    protected from overwrite on a repeat upsert -- unchanged by this
    fix, per the M3F audit's original scraped_at bug documentation."""
    external_id = _uid()
    _cleanup(local_db_session, external_id)
    try:
        upsert_early_signal_events(
            local_db_session,
            [
                _row(
                    external_id,
                    address="123 Original St",
                    applicant="Original Applicant Ltd",
                    project_value="1000000",
                )
            ],
        )

        upsert_early_signal_events(
            local_db_session,
            [
                _row(
                    external_id,
                    address="999 Changed Ave",
                    applicant="Changed Applicant Inc",
                    project_value="9999999",
                )
            ],
        )

        record = _fetch(local_db_session, external_id)
        assert record.address == "123 Original St"
        assert record.applicant == "Original Applicant Ltd"
        assert record.project_value == "1000000"
    finally:
        _cleanup(local_db_session, external_id)


def test_other_fields_still_update_on_repeat_upsert(local_db_session: Session):
    """Proof 4: every other allowed field must still update on repeat
    upsert -- the fix only changes scraped_at's treatment, nothing else
    in the upsert logic."""
    external_id = _uid()
    _cleanup(local_db_session, external_id)
    try:
        upsert_early_signal_events(local_db_session, [_row(external_id)])

        upsert_early_signal_events(
            local_db_session,
            [
                _row(
                    external_id,
                    transaction_date="2026-06-15",
                    municipality="Burnaby",
                    region="North Shore",
                    property_type="Industrial",
                    signal_type="development_permit_application",
                    url_link="https://example.test/project/1-updated",
                )
            ],
        )

        record = _fetch(local_db_session, external_id)
        assert record.transaction_date == "2026-06-15"
        assert record.municipality == "Burnaby"
        assert record.region == "North Shore"
        assert record.property_type == "Industrial"
        assert record.signal_type == "development_permit_application"
        assert record.url_link == "https://example.test/project/1-updated"
    finally:
        _cleanup(local_db_session, external_id)
