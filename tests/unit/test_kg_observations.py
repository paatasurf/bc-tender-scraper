"""Unit tests for KG Observation spine (Phase 1)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import KgObservation, KgOutboxEvent
from pipeline.kg.adapters.permit import (
    PermitObservationAdapter,
    build_permit_payload,
    derive_permit_external_id,
)
from pipeline.kg.constants import OBSERVATION_STATUS_ACTIVE, OBSERVATION_STATUS_SUPERSEDED
from pipeline.kg.domain import ObservationDraft
from pipeline.kg.flags import dual_write_enabled
from pipeline.kg.hashing import content_hash_for_payload
from pipeline.kg.store import record_observation


def test_content_hash_is_deterministic():
    payload = {"b": 2, "a": 1, "nested": {"z": 9, "y": 8}}
    assert content_hash_for_payload(payload) == content_hash_for_payload({"a": 1, "b": 2, "nested": {"y": 8, "z": 9}})


def test_derive_permit_external_id_uses_key_when_present():
    row = {"external_id": "BP-2025-001", "address": "1 Main", "project_value": "1000"}
    assert derive_permit_external_id(row, source="vancouver") == "BP-2025-001"


def test_derive_permit_external_id_fingerprint_when_blank():
    row = {
        "external_id": "",
        "address": "3380 VANNESS AVENUE",
        "project_value": "144787150.0",
        "applicant": "Tijana Sljivic",
    }
    ext = derive_permit_external_id(row, source="vancouver")
    assert ext.startswith("fp:")
    assert derive_permit_external_id(row, source="vancouver") == ext


def test_derive_permit_external_id_requires_address_and_value_for_fingerprint():
    with pytest.raises(ValueError):
        derive_permit_external_id({"external_id": "", "address": "", "project_value": ""}, source="vancouver")


def test_permit_adapter_builds_draft():
    row = {
        "external_id": "BP-1",
        "address": "1 Main St",
        "project_value": "50000",
        "applicant": "Acme Ltd",
        "contractor": "Acme Ltd",
        "source": "vancouver",
        "city": "Vancouver",
    }
    drafts = PermitObservationAdapter().to_drafts(row, source="vancouver")
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.source == "vancouver"
    assert draft.external_id == "BP-1"
    assert draft.entity_type == "permit"
    assert draft.payload["observation_kind"] == "permit_import_row"


def test_build_permit_payload_includes_resolution_metadata():
    row = {
        "external_id": "BP-2",
        "address": "2 Main",
        "project_value": "100",
        "applicant": "Bob",
        "company_id": 8638,
        "canonical_merge_method": "contractor",
        "canonical_merge_confidence": 0.9,
    }
    payload = build_permit_payload(row, source="surrey")
    assert payload["company_id"] == 8638
    assert payload["canonical_merge_method"] == "contractor"


def test_dual_write_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KG_OBSERVATION_DUAL_WRITE", raising=False)
    assert dual_write_enabled() is False


def test_dual_write_enabled_with_env(monkeypatch):
    monkeypatch.setenv("KG_OBSERVATION_DUAL_WRITE", "1")
    assert dual_write_enabled() is True


def test_permit_adapter_dual_write_respects_flag(monkeypatch):
    monkeypatch.delenv("KG_OBSERVATION_DUAL_WRITE", raising=False)
    stats = PermitObservationAdapter().dual_write_batch(
        Session(),
        [{"external_id": "X", "address": "A", "project_value": "1", "applicant": "B"}],
        commit=False,
        source="vancouver",
    )
    assert stats.skipped == 1
    assert stats.recorded == 0


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI (set CI_DATABASE_URL to enable)")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing KG observation tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def local_db_session() -> Session:
    import config.env  # noqa: F401
    from db.connection import init_db

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable for KG observation integration test")

    init_db()
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_record_observation_idempotent(local_db_session: Session):
    draft = ObservationDraft(
        source="test_kg_permit",
        external_id="obs-idempotent-1",
        payload={"address": "1 Test", "value": "100"},
        entity_type="permit",
        adapter_version="permit_v1",
    )
    first = record_observation(local_db_session, draft)
    local_db_session.commit()
    second = record_observation(local_db_session, draft)
    local_db_session.commit()

    assert first.created is True
    assert second.idempotent_replay is True
    assert first.observation_id == second.observation_id

    count = local_db_session.scalar(
        select(func.count())
        .select_from(KgObservation)
        .where(
            KgObservation.source == "test_kg_permit",
            KgObservation.external_id == "obs-idempotent-1",
        )
    )
    assert count == 1


def test_record_observation_supersedes_on_payload_change(local_db_session: Session):
    external_id = "obs-supersede-1"
    source = "test_kg_permit"

    first = record_observation(
        local_db_session,
        ObservationDraft(
            source=source,
            external_id=external_id,
            payload={"address": "1 Test", "value": "100"},
            entity_type="permit",
        ),
    )
    local_db_session.commit()

    second = record_observation(
        local_db_session,
        ObservationDraft(
            source=source,
            external_id=external_id,
            payload={"address": "1 Test", "value": "200"},
            entity_type="permit",
        ),
    )
    local_db_session.commit()

    assert second.superseded_prior is True
    assert second.observation_id != first.observation_id

    old = local_db_session.get(KgObservation, first.observation_id)
    new = local_db_session.get(KgObservation, second.observation_id)
    assert old is not None and new is not None
    assert old.status == OBSERVATION_STATUS_SUPERSEDED
    assert new.status == OBSERVATION_STATUS_ACTIVE
    assert old.superseded_by_id == new.id

    active_count = local_db_session.scalar(
        select(func.count())
        .select_from(KgObservation)
        .where(
            KgObservation.source == source,
            KgObservation.external_id == external_id,
            KgObservation.status == OBSERVATION_STATUS_ACTIVE,
        )
    )
    assert active_count == 1


def test_record_observation_enqueues_outbox(local_db_session: Session):
    result = record_observation(
        local_db_session,
        ObservationDraft(
            source="test_kg_permit",
            external_id="obs-outbox-1",
            payload={"k": "v"},
            entity_type="permit",
        ),
    )
    local_db_session.commit()

    outbox = local_db_session.scalar(
        select(KgOutboxEvent).where(KgOutboxEvent.aggregate_id == result.observation_id)
    )
    assert outbox is not None
    assert outbox.event_type == "ObservationRecorded"
    assert outbox.status == "pending"


@patch("db.permit_import._dual_write_permit_observations_safe")
def test_upsert_city_permits_calls_dual_write_hook(mock_dual_write, local_db_session: Session):
    from db.permit_import import upsert_city_permits

    rows = [
        {
            "external_id": "KG-TEST-1",
            "address": "99 Test Ave",
            "project_value": "1000",
            "applicant": "Test Builder Inc",
            "contractor": "Test Builder Inc",
            "source": "vancouver",
            "city": "Vancouver",
        }
    ]
    with patch("db.permit_import.resolve_permit_company_from_row") as mock_resolve:
        mock_resolve.return_value = type("R", (), {"company_id": None, "confidence": None, "method": ""})()
        imported = upsert_city_permits(local_db_session, rows, source="vancouver", full_refresh=False)

    assert imported == 1
    mock_dual_write.assert_called_once()
