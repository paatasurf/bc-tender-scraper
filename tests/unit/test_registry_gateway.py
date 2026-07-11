"""Unit tests for Registry Gateway (Phase 2 shadow/enforce)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from pipeline.registry_gateway.constants import (
    DECISION_CREATE,
    DECISION_REJECT,
    DECISION_REVIEW,
    REJECT_REASON_PERSON,
    SOURCE_PATH_POPULATE_AWARDS,
)
from pipeline.registry_gateway.flags import (
    gateway_active,
    gateway_enforce_enabled,
    gateway_shadow_enabled,
)
from pipeline.registry_gateway.gateway import RegistryGateway


@pytest.fixture(autouse=True)
def _clear_gateway_env(monkeypatch):
    monkeypatch.delenv("KG_GATEWAY_SHADOW", raising=False)
    monkeypatch.delenv("KG_GATEWAY_ENFORCE", raising=False)


def test_gateway_flags_default_off():
    assert gateway_shadow_enabled() is False
    assert gateway_enforce_enabled() is False
    assert gateway_active() is False


def _mock_session_with_ids() -> MagicMock:
    session = MagicMock()

    def _add(row: object) -> None:
        if getattr(row, "id", None) is None:
            row.id = 1  # type: ignore[attr-defined]

    session.add.side_effect = _add
    return session


def test_allow_resolver_create_person_blocked_in_enforce(monkeypatch):
    monkeypatch.setenv("KG_GATEWAY_ENFORCE", "1")
    session = _mock_session_with_ids()
    gateway = RegistryGateway(session)
    allowed, reason = gateway.allow_resolver_create(
        raw_name="Michael Yee",
        canonical_key="michael yee",
        trigger_source="permits:test",
        method="probable_person",
    )
    assert allowed is False
    assert reason == REJECT_REASON_PERSON


def test_filter_award_populate_blocks_in_enforce(monkeypatch):
    monkeypatch.setenv("KG_GATEWAY_ENFORCE", "1")
    session = _mock_session_with_ids()
    gateway = RegistryGateway(session)
    payload = [{"name": "New Vendor Ltd", "canonical_vendor_name": "new vendor ltd"}]
    filtered, stats = gateway.filter_award_populate_payload(payload)
    assert filtered == []
    assert stats["blocked"] == 1
    session.commit.assert_called()


def test_filter_award_populate_shadow_logs_but_allows(monkeypatch):
    monkeypatch.setenv("KG_GATEWAY_SHADOW", "1")
    session = _mock_session_with_ids()
    gateway = RegistryGateway(session)
    payload = [{"name": "New Vendor Ltd", "canonical_vendor_name": "new vendor ltd"}]
    filtered, stats = gateway.filter_award_populate_payload(payload)
    assert len(filtered) == 1
    assert stats["logged"] == 1
    assert stats["blocked"] == 0


def test_gateway_inactive_passes_payload_through():
    session = MagicMock()
    gateway = RegistryGateway(session)
    payload = [{"name": "Acme"}]
    filtered, stats = gateway.filter_award_populate_payload(payload)
    assert filtered == payload
    assert stats == {"blocked": 0, "logged": 0}
    session.commit.assert_not_called()


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing gateway tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def local_db_session():
    import config.env  # noqa: F401
    from db.connection import init_db, get_session_factory
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    init_db()
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_record_decision_persists(local_db_session, monkeypatch):
    monkeypatch.setenv("KG_GATEWAY_SHADOW", "1")
    from db.models import KgEngineDecisionRecord
    from pipeline.registry_gateway.store import record_engine_decision
    from pipeline.registry_gateway.domain import DecisionDraft

    result = record_engine_decision(
        local_db_session,
        DecisionDraft(
            decision=DECISION_CREATE,
            source_path=SOURCE_PATH_POPULATE_AWARDS,
            raw_identity="Test Co",
            gateway_mode="shadow",
            legacy_proceeded=True,
        ),
    )
    local_db_session.commit()
    row = local_db_session.get(KgEngineDecisionRecord, result.record_id)
    assert row is not None
    assert row.decision == DECISION_CREATE
