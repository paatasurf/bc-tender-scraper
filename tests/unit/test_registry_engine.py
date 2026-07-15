"""Unit tests for Registry Engine Stage 1 (RE1) — decide() and shadow logging.

Mirrors the stub/mock conventions of tests/unit/test_company_resolution.py and
tests/unit/test_registry_gateway.py: no live database is required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pipeline.company_resolution import CompanyResolver
from pipeline.identity_parser import RelationshipType
from pipeline.registry_engine.constants import (
    DECISION_CREATE,
    DECISION_MATCH,
    DECISION_REJECT,
    REGISTRY_CONFIDENCE_LOW,
    REGISTRY_CONFIDENCE_MEDIUM,
    REJECT_REASON_EMPTY,
    REJECT_REASON_PERSON,
)
from pipeline.registry_engine.decide import decide
from pipeline.registry_engine.flags import ENV_REGISTRY_ENGINE_SHADOW
from pipeline.registry_engine.store import record_shadow_decision


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _SessionStub:
    """Read-only company index, matching test_company_resolution.py's pattern."""

    def __init__(self, companies):
        self._companies = companies
        self.add_calls: list[object] = []

    def scalars(self, _q):
        return _ScalarsResult(self._companies)

    def execute(self, _q):
        class _Empty:
            def scalar_one_or_none(self):
                return None

            def all(self):
                return []

        return _Empty()

    def flush(self):
        return None

    def add(self, obj):
        # decide() must never reach this — CompanyResolver._create_company is
        # the only thing that calls session.add() for a new Company row.
        self.add_calls.append(obj)

    def get(self, *_args, **_kwargs):
        return None


def _company_row(company_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=company_id,
        name=name,
        display_name=name,
        canonical_vendor_name="",
        entity_role="canonical",
        canonical_company_id=None,
        total_value=100.0,
        total_award_value=0.0,
        total_projects=5,
    )


def _resolver(companies: list[SimpleNamespace]) -> tuple[CompanyResolver, _SessionStub]:
    session = _SessionStub(companies)
    return CompanyResolver(session), session


def test_decide_match_returns_existing_company():
    resolver, session = _resolver([_company_row(1, "Acme Construction Ltd.")])
    decision = decide(
        "Acme Construction Ltd.", resolver=resolver, source="test", city="Vancouver"
    )

    assert decision.decision == DECISION_MATCH
    assert decision.company_id == 1
    assert session.add_calls == []


def test_decide_create_shadow_when_no_existing_company():
    resolver, session = _resolver([])
    decision = decide(
        "Brand New Contractor Ltd.", resolver=resolver, source="test", city="Vancouver"
    )

    assert decision.decision == DECISION_CREATE
    assert decision.company_id is None
    # Stage 1 must never actually create a company — shadow-log only.
    assert session.add_calls == []


def test_decide_never_mutates_even_when_resolver_would_create():
    """Regression guard: decide() is read-only regardless of match outcome."""
    resolver, session = _resolver([])
    decide("Another New Vendor Inc.", resolver=resolver, source="test")
    assert session.add_calls == []


def test_decide_reject_person_name():
    resolver, _session = _resolver([])
    decision = decide("Michael Yee", resolver=resolver, source="test", city="Vancouver")

    assert decision.decision == DECISION_REJECT
    assert decision.reject_reason == REJECT_REASON_PERSON


def test_decide_reject_empty_identity():
    resolver, _session = _resolver([])
    decision = decide("   ", resolver=resolver, source="test")

    assert decision.decision == DECISION_REJECT
    assert decision.reject_reason == REJECT_REASON_EMPTY


def test_decide_registry_confidence_never_exceeds_medium():
    """Stage 1 has not cross-checked OrgBook/ODB, so it may not claim high/verified."""
    resolver, _session = _resolver([])
    decision = decide(
        "Acme Construction Ltd.",
        resolver=resolver,
        source="test",
        city="Vancouver",
        province="BC",
    )

    assert decision.registry_confidence in {
        REGISTRY_CONFIDENCE_MEDIUM,
        REGISTRY_CONFIDENCE_LOW,
    }
    assert decision.registry_confidence not in {"verified", "high"}


def test_decide_uses_canonical_parser_not_legacy():
    """ADR-8: decide() parses via identity_parser, not resolve_company_name/parse_name."""
    resolver, _session = _resolver([])
    decision = decide(
        "Smith Roofing DBA Smith & Sons", resolver=resolver, source="test"
    )
    assert decision.parsed_identity.parser_version
    assert decision.parsed_identity.relationship_type in RelationshipType


def test_registry_engine_reuses_gateway_decision_constants():
    """ADR-3: exactly one decision vocabulary, not a second copy of the same strings."""
    from pipeline.registry_gateway import constants as gateway_constants
    from pipeline.registry_engine import constants as engine_constants

    assert engine_constants.DECISION_MATCH == gateway_constants.DECISION_MATCH
    assert engine_constants.DECISION_CREATE == gateway_constants.DECISION_CREATE
    assert engine_constants.DECISION_MERGE == gateway_constants.DECISION_MERGE
    assert engine_constants.DECISION_REJECT == gateway_constants.DECISION_REJECT


# --- shadow logging -----------------------------------------------------------


def _mock_session() -> MagicMock:
    session = MagicMock()

    def _add(row: object) -> None:
        if getattr(row, "id", None) is None:
            row.id = 1  # type: ignore[attr-defined]

    session.add.side_effect = _add
    return session


@pytest.fixture(autouse=True)
def _clear_engine_env(monkeypatch):
    monkeypatch.delenv(ENV_REGISTRY_ENGINE_SHADOW, raising=False)


def test_record_shadow_decision_noop_when_flag_disabled():
    resolver, _session = _resolver([])
    decision = decide("Brand New Contractor Ltd.", resolver=resolver, source="test")

    session = _mock_session()
    result = record_shadow_decision(session, decision, trigger_source="permits:test")

    assert result is None
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_record_shadow_decision_writes_when_flag_enabled(monkeypatch):
    monkeypatch.setenv(ENV_REGISTRY_ENGINE_SHADOW, "1")
    resolver, _session = _resolver([_company_row(7, "Acme Construction Ltd.")])
    decision = decide(
        "Acme Construction Ltd.", resolver=resolver, source="test", city="Vancouver"
    )

    session = _mock_session()
    result = record_shadow_decision(session, decision, trigger_source="permits:test")

    assert result is not None
    assert result.decision == DECISION_MATCH
    session.add.assert_called_once()
    session.commit.assert_called_once()


def test_company_and_registry_pin_models_expose_stage1_columns():
    """Migration 028 <-> ORM model sanity check (no live DB required)."""
    from db.models import Company, RegistryPin

    company_columns = {c.name for c in Company.__table__.columns}
    for expected in (
        "public_id",
        "legal_name",
        "operating_name",
        "business_number",
        "registry_status",
        "verification_level",
        "registry_confidence",
    ):
        assert expected in company_columns

    pin_columns = {c.name for c in RegistryPin.__table__.columns}
    assert {
        "id",
        "company_id",
        "pin_key",
        "reason",
        "created_by",
        "created_at",
    } <= pin_columns


def test_record_shadow_decision_swallows_persist_errors(monkeypatch):
    monkeypatch.setenv(ENV_REGISTRY_ENGINE_SHADOW, "1")
    resolver, _session = _resolver([])
    decision = decide("Brand New Contractor Ltd.", resolver=resolver, source="test")

    session = MagicMock()
    session.add.side_effect = RuntimeError("boom")

    result = record_shadow_decision(session, decision, trigger_source="permits:test")

    assert result is None
    session.rollback.assert_called_once()
