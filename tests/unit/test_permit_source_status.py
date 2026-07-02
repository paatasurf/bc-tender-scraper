"""Unit tests for permit source_status_raw guardrails (no false status ingestion)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from api import internal as internal_api
from db.models import Permit
from db.permit_import import upsert_city_permits
from db.permit_source_status import (
    FUTURE_STATUS_SOURCES,
    backfill_permit_source_status,
)


def _require_local_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing permit status tests against production DATABASE_URL")
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


def test_backfill_is_noop_until_plpos(local_db_session: Session):
    result = backfill_permit_source_status(local_db_session, only_empty=True)
    assert result["status"] == "no_status_source_configured"
    assert result["totals"]["updated"] == 0
    assert "vancouver" in result["future_status_sources"]
    assert result["future_status_sources"]["vancouver"]["candidate"] == "PLPOS"


def test_upsert_city_permits_does_not_overwrite_source_status_raw(local_db_session: Session):
    external_id = "BP-STATUS-SKIP-1"
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
    assert row.source_status_raw == ""

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


def test_future_status_sources_documents_plpos_backlog():
    assert FUTURE_STATUS_SOURCES["vancouver"]["status"] == "backlog"
    assert FUTURE_STATUS_SOURCES["vancouver"]["candidate"] == "PLPOS"
