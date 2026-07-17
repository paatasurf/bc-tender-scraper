"""DB-backed and static tests for pipeline.registry_engine.claims.gateway
and gateway_capability (PR-B2 -- the Claims Gateway, hardened).

Local Postgres only -- skipped when unavailable or when DATABASE_URL resolves
to production (same convention as tests/unit/test_classification_claims_*).
Applies the real migration 029 schema per test, seeds exactly one throwaway
company row and the rule_set_versions it needs, and cleans everything up
afterward.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.classification_claims_ddl import classification_claims_table_names
from db.classification_claims_migration import apply_classification_claims_migration
from db.models import Company
from pipeline.registry_engine.claims.domain import (
    ActorType,
    ClaimType,
    EventType,
    InvalidClaimEventError,
    SourceType,
)
from pipeline.registry_engine.claims.gateway import (
    ClaimAlreadyExistsResult,
    ClaimDryRunResult,
    ClaimNotFoundError,
    ClaimSubmitted,
    CompanyNotFoundError,
    CrossScopeRelatedClaimError,
    EvidenceInsertFailedError,
    IncompatibleRuleSetVersionError,
    InvalidClaimTimestampsError,
    InvalidClaimValueError,
    InvalidEvidenceLocatorError,
    InvalidEvidenceSourceError,
    InvalidPredicateForClaimTypeError,
    LicenceSourceNotAllowedError,
    RecordedEvent,
    RelatedClaimNotFoundError,
    RuleSetVersionNotFoundError,
    TerminalEventAlreadyExistsError,
    record_event,
    submit_claim,
)
from pipeline.registry_engine.claims.gateway_capability import (
    ClaimsWriteCapability,
    UnauthorizedWriteError,
    _unwrap_engine,
    acquire_claims_write_capability,
)

GATEWAY_PATH = (
    Path(__file__).resolve().parents[2]
    / "pipeline"
    / "registry_engine"
    / "claims"
    / "gateway.py"
)
GATEWAY_CAPABILITY_PATH = GATEWAY_PATH.parent / "gateway_capability.py"


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing claims gateway tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def claims_engine():
    import config.env  # noqa: F401

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    def _drop_all():
        with engine.begin() as conn:
            # Drop the six claims tables FIRST -- classification_claims.company_id
            # FKs into companies, so the throwaway test company row below can
            # only be deleted safely once those FK references are gone.
            for name in classification_claims_table_names():
                conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))
            conn.execute(
                text(
                    "DELETE FROM companies WHERE name LIKE '__test_claims_gateway_company%'"
                )
            )

    _drop_all()
    try:
        yield engine
    finally:
        _drop_all()
        engine.dispose()


@pytest.fixture()
def claims_schema(claims_engine):
    apply_classification_claims_migration(claims_engine)
    return claims_engine


@dataclass(frozen=True)
class _SeededEnv:
    engine: object
    company_id: int
    sector_rule_set_id: str
    licence_rule_set_id: str
    future_rule_set_id: str


@pytest.fixture()
def seeded_env(claims_schema):
    engine = claims_schema
    with Session(engine) as session:
        company = Company(name="__test_claims_gateway_company__")
        session.add(company)
        session.commit()
        company_id = company.id
    # One execute() per row, not a list of params -- SQLAlchemy's psycopg2
    # executemany batching mis-parses a `:name::jsonb` cast immediately after
    # a bound parameter (misreads the `::` as a second parameter marker).
    rule_set_rows = [
        {
            "id": "v1-sector-test",
            "claim_type": "sector_classification",
            "precedence": '{"licence_authority": 1}',
            "effective_from": datetime(2020, 1, 1, tzinfo=timezone.utc),
        },
        {
            "id": "v1-licence-test",
            "claim_type": "licence_registration",
            "precedence": '{"government_registry": 1}',
            "effective_from": datetime(2020, 1, 1, tzinfo=timezone.utc),
        },
        {
            "id": "v1-sector-future",
            "claim_type": "sector_classification",
            "precedence": '{"licence_authority": 1}',
            "effective_from": datetime(2999, 1, 1, tzinfo=timezone.utc),
        },
    ]
    with engine.begin() as conn:
        for row in rule_set_rows:
            conn.execute(
                text("""
                    INSERT INTO rule_set_versions (
                        rule_set_version_id, claim_type, description,
                        precedence_definition_json, source_reliability_defaults_json,
                        staleness_policy_json, effective_from
                    ) VALUES (
                        :id, :claim_type, '', CAST(:precedence AS jsonb), '{}'::jsonb, '{}'::jsonb, :effective_from
                    )
                    """),
                row,
            )
    # Cleanup (including this company row) happens centrally in claims_engine's
    # teardown, after the six claims tables (and their FKs into companies)
    # are dropped -- deleting the company here would fail with a FK violation
    # while any claim rows created during the test still reference it.
    yield _SeededEnv(
        engine=engine,
        company_id=company_id,
        sector_rule_set_id="v1-sector-test",
        licence_rule_set_id="v1-licence-test",
        future_rule_set_id="v1-sector-future",
    )


@pytest.fixture()
def write_capability(seeded_env):
    """A genuine capability from the real factory (acquire_claims_write_capability),
    which runs the actual Class C/D guard (db_safety.guard_destructive_db)
    against local Postgres. allow_production=False against a local
    DATABASE_URL never requires TTY confirmation -- same as every other
    Class D script's local-DB path -- so this is safe to call in tests."""
    capability = acquire_claims_write_capability(
        "test_claims_gateway", allow_production=False, operation="test write"
    )
    try:
        yield capability
    finally:
        _unwrap_engine(capability).dispose()


def _row_count(engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def _sector_claim_kwargs(env: _SeededEnv, **overrides) -> dict:
    now = datetime.now(timezone.utc)
    kwargs = dict(
        company_id=env.company_id,
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        predicate="dominant_sector",
        value_json={"sector": "roofing"},
        source_type=SourceType.LICENCE_AUTHORITY,
        source_reliability=0.9,
        extraction_confidence=0.8,
        extraction_method="test_extractor_v1",
        rule_set_version_id=env.sector_rule_set_id,
        evidence_source="licence_authority_raw",
        evidence_locator={"url": "https://example.test/permit/123"},
        observed_at=now - timedelta(days=1),
        effective_at=now,
    )
    kwargs.update(overrides)
    return kwargs


def _licence_claim_kwargs(env: _SeededEnv, **overrides) -> dict:
    now = datetime.now(timezone.utc)
    kwargs = dict(
        company_id=env.company_id,
        claim_type=ClaimType.LICENCE_REGISTRATION,
        predicate="licence_identifier",
        value_json={
            "licence_identifier": "BC-12345",
            "issuing_authority": "City of Surrey",
            "status": "active",
            "expiry_date": None,
        },
        source_type=SourceType.GOVERNMENT_REGISTRY,
        source_reliability=0.95,
        extraction_confidence=0.9,
        extraction_method="test_extractor_v1",
        rule_set_version_id=env.licence_rule_set_id,
        evidence_source="government_registry_raw",
        evidence_locator={"url": "https://example.test/registry/456"},
        observed_at=now - timedelta(days=1),
        effective_at=now,
    )
    kwargs.update(overrides)
    return kwargs


def _assert_rejected_in_both_modes(
    seeded_env, write_capability, kwargs, error_cls
) -> None:
    """The shared-validator requirement: dry_run=True and dry_run=False must
    raise the exact same typed error for the exact same bad payload, and
    neither writes anything."""
    with pytest.raises(error_cls):
        submit_claim(seeded_env.engine, **kwargs, dry_run=True)
    assert _row_count(seeded_env.engine, "classification_claims") == 0

    with pytest.raises(error_cls):
        submit_claim(write_capability, **kwargs, dry_run=False)
    assert _row_count(seeded_env.engine, "classification_claims") == 0


# --- submit_claim: dry-run ----------------------------------------------------------


def test_dry_run_default_creates_zero_rows(seeded_env):
    """dry_run=True is the default -- an unspecified dry_run must never write."""
    result = submit_claim(seeded_env.engine, **_sector_claim_kwargs(seeded_env))
    assert isinstance(result, ClaimDryRunResult)
    assert result.would_create is True
    assert result.existing_claim_id is None
    assert _row_count(seeded_env.engine, "classification_claims") == 0
    assert _row_count(seeded_env.engine, "claim_evidence") == 0


def test_dry_run_explicit_creates_zero_rows(seeded_env):
    result = submit_claim(
        seeded_env.engine, **_sector_claim_kwargs(seeded_env), dry_run=True
    )
    assert isinstance(result, ClaimDryRunResult)
    assert _row_count(seeded_env.engine, "classification_claims") == 0


# --- submit_claim: apply -------------------------------------------------------------


def test_apply_creates_exactly_one_claim_and_one_evidence_row(
    seeded_env, write_capability
):
    result = submit_claim(
        write_capability, **_sector_claim_kwargs(seeded_env), dry_run=False
    )
    assert isinstance(result, ClaimSubmitted)
    assert _row_count(seeded_env.engine, "classification_claims") == 1
    assert _row_count(seeded_env.engine, "claim_evidence") == 1

    with seeded_env.engine.connect() as conn:
        claim_row = conn.execute(
            text(
                "SELECT claim_id, primary_evidence_content_hash FROM classification_claims"
            )
        ).first()
        evidence_row = conn.execute(
            text("SELECT claim_id, content_hash FROM claim_evidence")
        ).first()
    assert str(claim_row[0]) == result.claim_id
    assert claim_row[1] == result.content_hash
    assert str(evidence_row[0]) == result.claim_id
    assert evidence_row[1] == result.content_hash


def test_repeated_submit_is_idempotent(seeded_env, write_capability):
    kwargs = _sector_claim_kwargs(seeded_env)
    first = submit_claim(write_capability, **kwargs, dry_run=False)
    second = submit_claim(write_capability, **kwargs, dry_run=False)

    assert isinstance(first, ClaimSubmitted)
    assert isinstance(second, ClaimAlreadyExistsResult)
    assert second.claim_id == first.claim_id
    assert second.idempotency_key == first.idempotency_key
    assert _row_count(seeded_env.engine, "classification_claims") == 1
    assert _row_count(seeded_env.engine, "claim_evidence") == 1


def test_evidence_insert_failure_rolls_back_the_claim(
    seeded_env, write_capability, monkeypatch
):
    """Defense-in-depth proof: evidence_source vocabulary is now validated
    *before* any DB round trip (item #2 hardening), so a bad evidence_source
    can no longer reach the real INSERT through the public API. To still
    prove the transactional rollback guarantee itself -- not just the
    pre-check -- this simulates a pre-validation gap via monkeypatch (as if
    a future change forgot to call it) and confirms the DB CHECK constraint
    and the `except IntegrityError` handler still catch it, still roll back
    the already-issued claim insert, and still raise the typed error."""
    import pipeline.registry_engine.claims.gateway as gateway_module

    monkeypatch.setattr(
        gateway_module, "_validate_evidence_source", lambda *_a, **_k: None
    )
    kwargs = _sector_claim_kwargs(seeded_env, evidence_source="not_a_real_source")
    with pytest.raises(EvidenceInsertFailedError):
        submit_claim(write_capability, **kwargs, dry_run=False)
    assert _row_count(seeded_env.engine, "classification_claims") == 0
    assert _row_count(seeded_env.engine, "claim_evidence") == 0


@pytest.mark.parametrize("dry_run", [True, False])
def test_dangling_company_is_rejected(seeded_env, write_capability, dry_run):
    kwargs = _sector_claim_kwargs(seeded_env, company_id=999_999_999)
    target = seeded_env.engine if dry_run else write_capability
    with pytest.raises(CompanyNotFoundError):
        submit_claim(target, **kwargs, dry_run=dry_run)
    assert _row_count(seeded_env.engine, "classification_claims") == 0


@pytest.mark.parametrize("dry_run", [True, False])
def test_dangling_rule_set_is_rejected(seeded_env, write_capability, dry_run):
    kwargs = _sector_claim_kwargs(seeded_env, rule_set_version_id="does-not-exist")
    target = seeded_env.engine if dry_run else write_capability
    with pytest.raises(RuleSetVersionNotFoundError):
        submit_claim(target, **kwargs, dry_run=dry_run)
    assert _row_count(seeded_env.engine, "classification_claims") == 0


def test_incompatible_claim_type_rule_set_is_rejected(seeded_env, write_capability):
    """The rule set exists but governs a different claim_type."""
    kwargs = _sector_claim_kwargs(
        seeded_env, rule_set_version_id=seeded_env.licence_rule_set_id
    )
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, IncompatibleRuleSetVersionError
    )


def test_future_rule_set_is_rejected(seeded_env, write_capability):
    kwargs = _sector_claim_kwargs(
        seeded_env, rule_set_version_id=seeded_env.future_rule_set_id
    )
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, IncompatibleRuleSetVersionError
    )


def test_licence_source_restriction_is_enforced(seeded_env, write_capability):
    kwargs = _licence_claim_kwargs(seeded_env, source_type=SourceType.AI_INFERENCE)
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, LicenceSourceNotAllowedError
    )


def test_predicate_must_match_claim_type(seeded_env, write_capability):
    kwargs = _sector_claim_kwargs(seeded_env, predicate="licence_identifier")
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidPredicateForClaimTypeError
    )


def test_licence_registration_claim_applies_cleanly(seeded_env, write_capability):
    result = submit_claim(
        write_capability, **_licence_claim_kwargs(seeded_env), dry_run=False
    )
    assert isinstance(result, ClaimSubmitted)
    assert _row_count(seeded_env.engine, "classification_claims") == 1


# --- submit_claim: full validation parity matrix (item #2) ---------------------------


@pytest.mark.parametrize(
    "override_key, override_value",
    [
        ("evidence_source", "not_a_real_source"),
        ("evidence_source", ""),
    ],
)
def test_evidence_source_validation_matrix(
    seeded_env, write_capability, override_key, override_value
):
    kwargs = _sector_claim_kwargs(seeded_env, **{override_key: override_value})
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidEvidenceSourceError
    )


@pytest.mark.parametrize("bad_locator", [{}, "not-a-dict", None, []])
def test_evidence_locator_validation_matrix(seeded_env, write_capability, bad_locator):
    kwargs = _sector_claim_kwargs(seeded_env, evidence_locator=bad_locator)
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidEvidenceLocatorError
    )


def test_effective_at_before_observed_at_is_rejected(seeded_env, write_capability):
    now = datetime.now(timezone.utc)
    kwargs = _sector_claim_kwargs(
        seeded_env, observed_at=now, effective_at=now - timedelta(days=1)
    )
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidClaimTimestampsError
    )


def test_effective_at_equal_to_observed_at_is_accepted(seeded_env):
    now = datetime.now(timezone.utc)
    kwargs = _sector_claim_kwargs(seeded_env, observed_at=now, effective_at=now)
    result = submit_claim(seeded_env.engine, **kwargs, dry_run=True)
    assert isinstance(result, ClaimDryRunResult)


@pytest.mark.parametrize(
    "value_json",
    [
        {},
        {"sector": ""},
        {"sector": 123},
        {"sector": "roofing", "extra": "nope"},
        {"not_sector": "roofing"},
    ],
    ids=["empty", "empty_string", "wrong_type", "extra_key", "wrong_key"],
)
def test_dominant_sector_value_json_shape_matrix(
    seeded_env, write_capability, value_json
):
    kwargs = _sector_claim_kwargs(seeded_env, value_json=value_json)
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidClaimValueError
    )


@pytest.mark.parametrize(
    "value_json",
    [
        {},
        {"trade": ""},
        {"trade": "electrical", "extra": "nope"},
    ],
    ids=["empty", "empty_string", "extra_key"],
)
def test_primary_trade_value_json_shape_matrix(
    seeded_env, write_capability, value_json
):
    kwargs = _sector_claim_kwargs(
        seeded_env, predicate="primary_trade", value_json=value_json
    )
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidClaimValueError
    )


@pytest.mark.parametrize(
    "value_json",
    [
        {},
        {"business_number": ""},
        {"business_number": "123456789", "extra": "nope"},
    ],
    ids=["empty", "empty_string", "extra_key"],
)
def test_business_number_value_json_shape_matrix(
    seeded_env, write_capability, value_json
):
    kwargs = _licence_claim_kwargs(
        seeded_env, predicate="business_number", value_json=value_json
    )
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidClaimValueError
    )


@pytest.mark.parametrize(
    "value_json",
    [
        {},
        {
            "licence_identifier": "BC-1",
            "issuing_authority": "City",
            "status": "active",
        },
        {
            "licence_identifier": "BC-1",
            "issuing_authority": "City",
            "status": "bogus",
            "expiry_date": None,
        },
        {
            "licence_identifier": "BC-1",
            "issuing_authority": "City",
            "status": "active",
            "expiry_date": "not-a-date",
        },
        {
            "licence_identifier": "",
            "issuing_authority": "City",
            "status": "active",
            "expiry_date": None,
        },
        {
            "licence_identifier": "BC-1",
            "issuing_authority": "City",
            "status": "active",
            "expiry_date": None,
            "extra": "x",
        },
    ],
    ids=[
        "empty",
        "missing_expiry_date_key",
        "bad_status",
        "bad_expiry_date_format",
        "empty_licence_identifier",
        "extra_key",
    ],
)
def test_licence_identifier_value_json_shape_matrix(
    seeded_env, write_capability, value_json
):
    kwargs = _licence_claim_kwargs(seeded_env, value_json=value_json)
    _assert_rejected_in_both_modes(
        seeded_env, write_capability, kwargs, InvalidClaimValueError
    )


def test_licence_identifier_accepts_valid_iso_expiry_date(seeded_env):
    kwargs = _licence_claim_kwargs(
        seeded_env,
        value_json={
            "licence_identifier": "BC-1",
            "issuing_authority": "City",
            "status": "active",
            "expiry_date": "2030-01-01",
        },
    )
    result = submit_claim(seeded_env.engine, **kwargs, dry_run=True)
    assert isinstance(result, ClaimDryRunResult)
    assert result.would_create is True


# --- record_event: helpers ------------------------------------------------------------


def _submit_and_get_claim_id(env: _SeededEnv, capability, **overrides) -> str:
    result = submit_claim(
        capability, **_sector_claim_kwargs(env, **overrides), dry_run=False
    )
    assert isinstance(result, ClaimSubmitted)
    return result.claim_id


# --- record_event: transition matrix --------------------------------------------------


def test_record_event_rejected_succeeds(seeded_env, write_capability):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    result = record_event(
        write_capability,
        claim_id=claim_id,
        event_type=EventType.REJECTED,
        related_claim_id=None,
        actor_type=ActorType.SYSTEM,
        actor_id="test-system",
        rule_set_version_id=seeded_env.sector_rule_set_id,
        event_at=datetime.now(timezone.utc),
    )
    assert isinstance(result, RecordedEvent)
    assert result.event_type == EventType.REJECTED
    assert _row_count(seeded_env.engine, "claim_events") == 1


def test_record_event_adjudicated_requires_human_actor(seeded_env, write_capability):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    with pytest.raises(InvalidClaimEventError):
        record_event(
            write_capability,
            claim_id=claim_id,
            event_type=EventType.ADJUDICATED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.sector_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 0


def test_record_event_adjudicated_succeeds_for_human_actor(
    seeded_env, write_capability
):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    result = record_event(
        write_capability,
        claim_id=claim_id,
        event_type=EventType.ADJUDICATED,
        related_claim_id=None,
        actor_type=ActorType.HUMAN,
        actor_id="reviewer@example.test",
        rule_set_version_id=seeded_env.sector_rule_set_id,
        event_at=datetime.now(timezone.utc),
    )
    assert result.event_type == EventType.ADJUDICATED
    assert _row_count(seeded_env.engine, "claim_events") == 1


def test_record_event_superseded_requires_same_scope_related_claim(
    seeded_env, write_capability
):
    """Same (company_id, claim_type, predicate) scope, but a distinct claim
    (different extraction_method -> different idempotency_key) -- this is
    exactly the intended superseded-by-a-correction scenario."""
    claim_a = _submit_and_get_claim_id(seeded_env, write_capability)
    claim_b = _submit_and_get_claim_id(
        seeded_env, write_capability, extraction_method="test_extractor_v2_correction"
    )
    result = record_event(
        write_capability,
        claim_id=claim_a,
        event_type=EventType.SUPERSEDED,
        related_claim_id=claim_b,
        actor_type=ActorType.SYSTEM,
        actor_id="test-system",
        rule_set_version_id=seeded_env.sector_rule_set_id,
        event_at=datetime.now(timezone.utc),
    )
    assert result.event_type == EventType.SUPERSEDED
    assert _row_count(seeded_env.engine, "claim_events") == 1


def test_record_event_cross_scope_superseded_target_is_rejected(
    seeded_env, write_capability
):
    """related_claim_id belongs to a different (company, claim_type, predicate)
    scope -- must be rejected, not silently accepted."""
    with Session(seeded_env.engine) as session:
        other_company = Company(name="__test_claims_gateway_company_other__")
        session.add(other_company)
        session.commit()
        other_company_id = other_company.id
    # Cleanup happens centrally in claims_engine's teardown (see its comment)
    # -- same reason the primary seeded company isn't deleted here either.
    claim_a = _submit_and_get_claim_id(seeded_env, write_capability)
    claim_other_company = _submit_and_get_claim_id(
        seeded_env, write_capability, company_id=other_company_id
    )
    with pytest.raises(CrossScopeRelatedClaimError):
        record_event(
            write_capability,
            claim_id=claim_a,
            event_type=EventType.SUPERSEDED,
            related_claim_id=claim_other_company,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.sector_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 0


def test_record_event_superseded_missing_related_claim_is_rejected(
    seeded_env, write_capability
):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    with pytest.raises(RelatedClaimNotFoundError):
        record_event(
            write_capability,
            claim_id=claim_id,
            event_type=EventType.SUPERSEDED,
            related_claim_id=str(__import__("uuid").uuid4()),
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.sector_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )


def test_record_event_unknown_claim_is_rejected(seeded_env, write_capability):
    with pytest.raises(ClaimNotFoundError):
        record_event(
            write_capability,
            claim_id=str(__import__("uuid").uuid4()),
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.sector_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )


def test_record_event_second_terminal_event_is_a_typed_error_not_integrity_error(
    seeded_env, write_capability
):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    record_event(
        write_capability,
        claim_id=claim_id,
        event_type=EventType.REJECTED,
        related_claim_id=None,
        actor_type=ActorType.SYSTEM,
        actor_id="test-system",
        rule_set_version_id=seeded_env.sector_rule_set_id,
        event_at=datetime.now(timezone.utc),
    )
    with pytest.raises(TerminalEventAlreadyExistsError):
        record_event(
            write_capability,
            claim_id=claim_id,
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.sector_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 1


# --- record_event: rule-set compatibility (item #3) -----------------------------------


def test_record_event_wrong_claim_type_rule_set_is_rejected(
    seeded_env, write_capability
):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    with pytest.raises(IncompatibleRuleSetVersionError):
        record_event(
            write_capability,
            claim_id=claim_id,
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.licence_rule_set_id,  # wrong claim_type
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 0


def test_record_event_future_rule_set_is_rejected(seeded_env, write_capability):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    with pytest.raises(IncompatibleRuleSetVersionError):
        record_event(
            write_capability,
            claim_id=claim_id,
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.future_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 0


def test_record_event_dangling_rule_set_is_rejected(seeded_env, write_capability):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    with pytest.raises(RuleSetVersionNotFoundError):
        record_event(
            write_capability,
            claim_id=claim_id,
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id="does-not-exist",
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 0


# --- record_event: concurrency (parent-row lock, not claim_events) --------------------


def test_concurrent_terminal_events_are_serialized_by_the_parent_claim_lock(
    seeded_env, write_capability
):
    """Proves record_event's SELECT ... FOR UPDATE on the PARENT claim row is
    what actually serializes two concurrent terminal-event attempts -- a
    second engine/thread trying to record_event() on the same claim must
    block until the first transaction's lock is released."""
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    database_url = _require_local_database_url()
    holder_engine = create_engine(database_url, connect_args={"connect_timeout": 3})

    lock_acquired = threading.Event()
    release_lock = threading.Event()
    holder_error: list[BaseException] = []

    def _hold_lock():
        try:
            with holder_engine.begin() as conn:
                conn.execute(
                    text(
                        "SELECT 1 FROM classification_claims WHERE claim_id = :id FOR UPDATE"
                    ),
                    {"id": claim_id},
                )
                lock_acquired.set()
                release_lock.wait(timeout=10)
        except BaseException as exc:  # noqa: BLE001
            holder_error.append(exc)
            lock_acquired.set()

    holder_thread = threading.Thread(target=_hold_lock)
    holder_thread.start()
    assert lock_acquired.wait(timeout=5), "holder never acquired the lock"
    assert not holder_error, f"holder thread failed: {holder_error}"

    contender_done = threading.Event()
    contender_result: list[object] = []
    contender_error: list[BaseException] = []

    def _contend():
        try:
            result = record_event(
                write_capability,
                claim_id=claim_id,
                event_type=EventType.REJECTED,
                related_claim_id=None,
                actor_type=ActorType.SYSTEM,
                actor_id="test-system",
                rule_set_version_id=seeded_env.sector_rule_set_id,
                event_at=datetime.now(timezone.utc),
            )
            contender_result.append(result)
        except BaseException as exc:  # noqa: BLE001
            contender_error.append(exc)
        finally:
            contender_done.set()

    contender_thread = threading.Thread(target=_contend)
    contender_thread.start()

    # While the holder keeps the lock, the contender must NOT complete.
    assert not contender_done.wait(timeout=1), (
        "record_event() completed while the parent claim row was still locked "
        "by another transaction -- the FOR UPDATE lock is not serializing"
    )

    release_lock.set()
    holder_thread.join(timeout=10)
    assert contender_done.wait(
        timeout=10
    ), "record_event() never completed after the lock was released"
    contender_thread.join(timeout=10)

    assert not contender_error, f"contender failed: {contender_error}"
    assert len(contender_result) == 1
    assert isinstance(contender_result[0], RecordedEvent)
    assert _row_count(seeded_env.engine, "claim_events") == 1
    holder_engine.dispose()


# --- write authorization: capability enforcement (item #1) ---------------------------


def test_submit_claim_apply_rejects_raw_engine(seeded_env):
    """dry_run=False with a raw Engine must fail closed before any SQL --
    not just before a write, before ANYTHING is sent to the DB."""
    kwargs = _sector_claim_kwargs(seeded_env)
    with pytest.raises(UnauthorizedWriteError):
        submit_claim(seeded_env.engine, **kwargs, dry_run=False)
    assert _row_count(seeded_env.engine, "classification_claims") == 0


def test_record_event_rejects_raw_engine(seeded_env, write_capability):
    claim_id = _submit_and_get_claim_id(seeded_env, write_capability)
    with pytest.raises(UnauthorizedWriteError):
        record_event(
            seeded_env.engine,
            claim_id=claim_id,
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_env.sector_rule_set_id,
            event_at=datetime.now(timezone.utc),
        )
    assert _row_count(seeded_env.engine, "claim_events") == 0


def test_direct_capability_construction_is_rejected(seeded_env):
    """Not a boolean flag: constructing ClaimsWriteCapability directly
    (bypassing acquire_claims_write_capability and its private token) must
    fail -- the only legitimate path is through the factory, which requires
    the Class C/D guard to succeed first."""
    with pytest.raises(UnauthorizedWriteError):
        ClaimsWriteCapability(_engine=seeded_env.engine, _token=object())


def test_capability_engine_cannot_be_reassigned(write_capability):
    """Frozen: the wrapped Engine can never be swapped out after the guard
    has run via normal attribute assignment."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        write_capability._engine = "fake"


def test_capability_does_not_expose_a_public_engine_attribute(write_capability):
    """The public capability API surface must not include a way to reach
    the wrapped Engine -- only the package-internal _unwrap_engine() (used
    by gateway.py) can. This is a fail-closed barrier against a caller
    grabbing the raw Engine off a capability and using it directly,
    bypassing every check submit_claim()/record_event() perform -- not a
    claim that Python can make this literally impossible for code that
    imports the private helper directly (see gateway_capability.py's module
    docstring)."""
    assert not hasattr(write_capability, "engine")
    public_attrs = [a for a in dir(write_capability) if not a.startswith("_")]
    assert public_attrs == []


def test_one_capability_serves_multiple_submissions_without_reguarding(
    seeded_env, write_capability
):
    """The guard runs once (in the write_capability fixture); this single
    capability is reused across two submissions and one event -- proving
    the batch-not-per-claim contract."""
    kwargs_a = _sector_claim_kwargs(seeded_env)
    kwargs_b = _sector_claim_kwargs(seeded_env, extraction_method="second_batch_item")
    result_a = submit_claim(write_capability, **kwargs_a, dry_run=False)
    result_b = submit_claim(write_capability, **kwargs_b, dry_run=False)
    assert isinstance(result_a, ClaimSubmitted)
    assert isinstance(result_b, ClaimSubmitted)

    record_event(
        write_capability,
        claim_id=result_a.claim_id,
        event_type=EventType.REJECTED,
        related_claim_id=None,
        actor_type=ActorType.SYSTEM,
        actor_id="test-system",
        rule_set_version_id=seeded_env.sector_rule_set_id,
        event_at=datetime.now(timezone.utc),
    )
    assert _row_count(seeded_env.engine, "classification_claims") == 2
    assert _row_count(seeded_env.engine, "claim_events") == 1


def test_acquire_write_capability_refuses_non_production_url_used_as_production(
    monkeypatch,
):
    """Proves the factory genuinely calls the Class C/D guard: pointing
    DATABASE_URL at a fake (never-real) production-shaped host without
    allow_production=True must refuse via the guard, and no capability is
    ever constructed."""
    monkeypatch.setenv("DB_PRODUCTION_HOSTS", "fake-prod-host.test")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:pass@fake-prod-host.test:5432/fakedb"
    )
    with pytest.raises(SystemExit):
        acquire_claims_write_capability("test_claims_gateway", allow_production=False)


def test_acquire_write_capability_refuses_production_without_real_tty(monkeypatch):
    """Even with allow_production=True, non-interactive stdin (like this
    test process) cannot satisfy the human confirmation gate -- no
    capability is ever constructed for a failed/absent confirmation."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/local_db")
    monkeypatch.setenv("DB_PRODUCTION_HOSTS", "fake-prod-host.test")
    monkeypatch.setenv(
        "DATABASE_URL_PRODUCTION",
        "postgresql://user:pass@fake-prod-host.test:5432/fakedb",
    )
    with pytest.raises(SystemExit):
        acquire_claims_write_capability("test_claims_gateway", allow_production=True)


# --- static: write guard / module boundaries ------------------------------------------


def test_write_guard_allows_only_select_and_insert() -> None:
    """Static guard: every `text(...)` SQL fragment in gateway.py must start
    with SELECT or INSERT -- no UPDATE/DELETE/DROP/ALTER/TRUNCATE anywhere."""
    tree = ast.parse(GATEWAY_PATH.read_text(encoding="utf-8"))
    forbidden = ("UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "CREATE ")
    checked = 0

    def _literal_text(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value for part in node.values if isinstance(part, ast.Constant)
            )
        return None

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
        ):
            sql = _literal_text(node.args[0])
            if sql is None:
                continue
            checked += 1
            upper = " ".join(sql.split()).upper()
            assert upper.startswith("SELECT") or upper.startswith(
                "INSERT"
            ), f"non-SELECT/INSERT SQL statement found: {sql!r}"
            for keyword in forbidden:
                assert (
                    keyword not in upper
                ), f"forbidden keyword {keyword!r} in: {sql!r}"

    assert checked >= 2, "expected multiple text(...) SQL statements to check"


def test_gateway_never_resolves_database_url_itself() -> None:
    """Structural safety proof: gateway.py must not import anything that
    could resolve DATABASE_URL_PRODUCTION on its own -- production access
    can only come through a ClaimsWriteCapability a caller already
    obtained. AST-based (checks actual imports, not the module docstring,
    which explains -- in prose -- exactly why these are absent; a substring
    check would false-positive on that)."""
    tree = ast.parse(GATEWAY_PATH.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)

    assert "os" not in imported_modules
    assert "get_env" not in imported_names
    assert "guard_destructive_db" not in imported_names
    assert "guard_destructive_db_from_args" not in imported_names
    assert "guard_registry_write_db" not in imported_names
    assert "guard_registry_write_db_from_args" not in imported_names
    assert "acquire_claims_write_capability" not in imported_names


def test_gateway_capability_module_imports_the_guard() -> None:
    """Complementary proof: the ONE place in this package allowed to touch
    the Class C/D guard is gateway_capability.py."""
    tree = ast.parse(GATEWAY_CAPABILITY_PATH.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
    assert "guard_destructive_db" in imported_names
