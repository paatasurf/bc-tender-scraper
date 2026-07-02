"""Unit tests for permit source_status_raw extraction and backfill."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from api import internal as internal_api
from db.models import Permit
from db.permit_source_status import (
    VANCOUVER_STATUS_SOURCE_FIELD,
    backfill_permit_source_status,
    extract_surrey_source_status,
    extract_vancouver_source_status,
)
from pipeline.permit_lifecycle_resolver import (
    PermitLifecycleSnapshot,
    evaluate_permit_lifecycle_transition,
    lifecycle_from_source_status,
)
from db.permit_lifecycle_constants import (
    PERMIT_LIFECYCLE_STATUS_ACTIVE,
    PERMIT_LIFECYCLE_STATUS_UNKNOWN,
)
from db.permit_import import upsert_city_permits


def _require_local_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing permit status backfill tests against production DATABASE_URL")
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


def test_extract_vancouver_source_status_from_permitcategory():
    assert (
        extract_vancouver_source_status({"permitcategory": "New Build - Standalone Laneway"})
        == "New Build - Standalone Laneway"
    )
    assert extract_vancouver_source_status({"permitcategory": None}) == ""
    assert extract_vancouver_source_status({}) == ""


def test_extract_surrey_source_status_from_permit_status_only():
    assert extract_surrey_source_status({"PermitStatus": "Finaled"}) == "Finaled"
    assert extract_surrey_source_status({"WorkDescription": "Renovation"}) == ""
    assert extract_surrey_source_status({}) == ""


def test_unmapped_vancouver_permitcategory_lifecycle_is_unknown():
    from datetime import datetime, timezone

    raw = "Renovation - Residential - Lower Complexity"
    assert lifecycle_from_source_status(raw) == PERMIT_LIFECYCLE_STATUS_UNKNOWN
    rule = evaluate_permit_lifecycle_transition(
        PermitLifecycleSnapshot(
            lifecycle_status=PERMIT_LIFECYCLE_STATUS_ACTIVE,
            is_active=True,
            lifecycle_status_override=None,
            source_status_raw=raw,
            issue_date="2026-01-01",
            application_date="2025-12-01",
        ),
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    assert rule == "source_status_unknown"


def test_upsert_city_permits_writes_source_status_raw(local_db_session: Session):
    external_id = "BP-STATUS-TEST-1"
    local_db_session.execute(
        text("DELETE FROM permits WHERE source = 'vancouver' AND external_id = :external_id"),
        {"external_id": external_id},
    )
    local_db_session.commit()

    upsert_city_permits(
        local_db_session,
        [
            {
                "external_id": external_id,
                "address": "1 Main St",
                "permit_type": "New Building",
                "project_value": "1000",
                "applicant": "Applicant",
                "issue_date": "2026-06-01",
                "application_date": "2026-05-01",
                "description": "Test",
                "contractor": "",
                "local_area": "",
                "source_status_raw": "Issued",
                "source": "vancouver",
                "city": "Vancouver",
            }
        ],
        source="vancouver",
        full_refresh=False,
    )

    row = local_db_session.scalar(
        select(Permit).where(Permit.source == "vancouver", Permit.external_id == external_id)
    )
    assert row is not None
    assert row.source_status_raw == "Issued"

    local_db_session.execute(
        text("DELETE FROM permits WHERE source = 'vancouver' AND external_id = :external_id"),
        {"external_id": external_id},
    )
    local_db_session.commit()


@patch("db.permit_source_status.collect_source_status_vocabularies")
@patch("db.permit_source_status._status_lookup_for_source")
def test_backfill_permit_source_status_idempotent(
    mock_lookup,
    mock_vocab,
    local_db_session: Session,
):
    mock_vocab.return_value = {"vancouver": ["Issued"], "surrey": []}
    external_id = "BP-BACKFILL-TEST-1"
    local_db_session.execute(
        text("DELETE FROM permits WHERE source = 'vancouver' AND external_id = :external_id"),
        {"external_id": external_id},
    )
    local_db_session.commit()

    permit = Permit(
        address="2 Main St",
        external_id=external_id,
        source="vancouver",
        city="Vancouver",
        issue_date="2026-06-01",
        source_status_raw="",
    )
    local_db_session.add(permit)
    local_db_session.commit()

    mock_lookup.return_value = {external_id: "Issued"}

    first = backfill_permit_source_status(local_db_session, only_empty=True, sources=("vancouver",))
    assert first["cities"]["vancouver"]["updated"] == 1
    assert first["cities"]["vancouver"]["after_set"] == 1

    second = backfill_permit_source_status(local_db_session, only_empty=True, sources=("vancouver",))
    assert second["cities"]["vancouver"]["updated"] == 0
    assert second["cities"]["vancouver"]["after_set"] == 1

    local_db_session.execute(
        text("DELETE FROM permits WHERE source = 'vancouver' AND external_id = :external_id"),
        {"external_id": external_id},
    )
    local_db_session.commit()


def test_backfill_permit_status_endpoint_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with pytest.raises(Exception) as exc:
            internal_api.backfill_permit_status(request)
    assert getattr(exc.value, "status_code", None) == 403


def test_vancouver_status_source_field_constant():
    assert VANCOUVER_STATUS_SOURCE_FIELD == "permitcategory"
