"""Pure unit tests for pipeline.registry_engine.claims.domain.

No database, no I/O.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.registry_engine.claims as claims_package
from pipeline.registry_engine.claims.domain import (
    ActorType,
    ClaimEvent,
    ClaimType,
    ClassificationClaim,
    EventType,
    InvalidClaimEventError,
    InvalidClassificationClaimError,
    InvalidRuleSetVersionError,
    LICENCE_REGISTRATION_PRECEDENCE_V1,
    RuleSetVersion,
    SECTOR_CLASSIFICATION_PRECEDENCE_V1,
    SourceType,
)

_NAIVE_TIME = datetime(2026, 1, 1)  # deliberately no tzinfo

_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _claim(**overrides) -> ClassificationClaim:
    base = dict(
        claim_id=str(uuid.uuid4()),
        company_id=1,
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        predicate="dominant_sector",
        value_json={"sector": "roofing"},
        source_type=SourceType.GOVERNMENT_REGISTRY,
        source_reliability=0.9,
        extraction_confidence=0.9,
        extraction_method="test-extractor:v1",
        rule_set_version_id="sector_classification_v1",
        primary_evidence_content_hash="a" * 64,
        observed_at=_BASE_TIME,
        effective_at=_BASE_TIME,
        extracted_at=_BASE_TIME,
        idempotency_key="b" * 64,
        created_at=_BASE_TIME,
    )
    base.update(overrides)
    return ClassificationClaim(**base)


def test_classification_claim_is_frozen():
    claim = _claim()
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.company_id = 99  # type: ignore[misc]


def test_claim_event_is_frozen():
    event = ClaimEvent(
        event_id=str(uuid.uuid4()),
        claim_id=str(uuid.uuid4()),
        event_type=EventType.REJECTED,
        related_claim_id=None,
        actor_type=ActorType.SYSTEM,
        actor_id="test",
        rationale=None,
        rule_set_version_id="sector_classification_v1",
        event_at=_BASE_TIME,
        created_at=_BASE_TIME,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.event_type = EventType.ADJUDICATED  # type: ignore[misc]


def test_event_type_has_no_non_terminal_member():
    assert {e.value for e in EventType} == {"superseded", "rejected", "adjudicated"}
    assert "reaffirmed" not in {e.value for e in EventType}


def test_claim_type_v1_scope():
    assert {c.value for c in ClaimType} == {
        "sector_classification",
        "licence_registration",
    }


def test_sector_precedence_constant_covers_every_source_type():
    assert set(SECTOR_CLASSIFICATION_PRECEDENCE_V1.keys()) == set(SourceType)
    assert sorted(SECTOR_CLASSIFICATION_PRECEDENCE_V1.values()) == [1, 2, 3, 4, 5, 6, 7]


def test_sector_precedence_matches_approved_ranking():
    assert SECTOR_CLASSIFICATION_PRECEDENCE_V1 == {
        SourceType.LICENCE_AUTHORITY: 1,
        SourceType.ASSOCIATION_DIRECTORY: 2,
        SourceType.GOVERNMENT_REGISTRY: 3,
        SourceType.OFFICIAL_WEBSITE: 4,
        SourceType.GOOGLE_BUSINESS_PROFILE: 5,
        SourceType.ACTIVITY_DERIVED: 6,
        SourceType.AI_INFERENCE: 7,
    }


def test_licence_precedence_restricted_to_two_tied_sources():
    assert LICENCE_REGISTRATION_PRECEDENCE_V1 == {
        SourceType.GOVERNMENT_REGISTRY: 1,
        SourceType.LICENCE_AUTHORITY: 1,
    }
    assert SourceType.ASSOCIATION_DIRECTORY not in LICENCE_REGISTRATION_PRECEDENCE_V1


def test_claims_package_has_no_sqlalchemy_or_db_imports():
    package_dir = pathlib.Path(claims_package.__file__).parent
    for path in package_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
            elif isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
        for name in imported_names:
            assert not name.startswith(
                "sqlalchemy"
            ), f"{path.name} imports sqlalchemy: {name}"
            assert name != "db" and not name.startswith(
                "db."
            ), f"{path.name} imports db.*: {name}"


def test_claims_package_never_opens_a_session_or_writes():
    package_dir = pathlib.Path(claims_package.__file__).parent
    for path in package_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "session.add(" not in source
        assert "session.commit(" not in source
        assert "session.execute(" not in source
        assert "get_session_factory" not in source


def _event(**overrides) -> ClaimEvent:
    base = dict(
        event_id=str(uuid.uuid4()),
        claim_id=str(uuid.uuid4()),
        event_type=EventType.REJECTED,
        related_claim_id=None,
        actor_type=ActorType.SYSTEM,
        actor_id="test",
        rationale=None,
        rule_set_version_id="sector_classification_v1",
        event_at=_BASE_TIME,
        created_at=_BASE_TIME,
    )
    base.update(overrides)
    return ClaimEvent(**base)


# --- human-only adjudication ---------------------------------------------------------


def test_system_adjudication_rejected():
    with pytest.raises(InvalidClaimEventError):
        _event(event_type=EventType.ADJUDICATED, actor_type=ActorType.SYSTEM)


def test_human_adjudication_accepted():
    event = _event(
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
        actor_id="reviewer-1",
    )
    assert event.actor_type == ActorType.HUMAN


# --- related_claim_id structural rules ------------------------------------------------


def test_claim_event_rejects_missing_related_claim_id_on_superseded():
    with pytest.raises(InvalidClaimEventError):
        _event(event_type=EventType.SUPERSEDED, related_claim_id=None)


def test_claim_event_rejects_self_referencing_related_claim_id():
    claim_id = str(uuid.uuid4())
    with pytest.raises(InvalidClaimEventError):
        _event(
            claim_id=claim_id,
            event_type=EventType.SUPERSEDED,
            related_claim_id=claim_id,
        )


def test_claim_event_accepts_related_claim_id_on_superseded():
    event = _event(event_type=EventType.SUPERSEDED, related_claim_id=str(uuid.uuid4()))
    assert event.related_claim_id is not None


@pytest.mark.parametrize("event_type", [EventType.REJECTED, EventType.ADJUDICATED])
def test_claim_event_rejects_related_claim_id_on_non_superseded(event_type):
    actor_type = (
        ActorType.HUMAN if event_type == EventType.ADJUDICATED else ActorType.SYSTEM
    )
    with pytest.raises(InvalidClaimEventError):
        _event(
            event_type=event_type,
            actor_type=actor_type,
            related_claim_id=str(uuid.uuid4()),
        )


# --- RuleSetVersion compatibility / structural validation -----------------------------


def test_rule_set_version_rejects_empty_precedence():
    with pytest.raises(InvalidRuleSetVersionError):
        RuleSetVersion(
            rule_set_version_id="x",
            claim_type=ClaimType.SECTOR_CLASSIFICATION,
            precedence={},
            staleness_threshold=timedelta(days=1),
            effective_from=_BASE_TIME,
        )


@pytest.mark.parametrize("bad_tier", [0, -1, 1.5, "1", True])
def test_rule_set_version_rejects_invalid_tier_value(bad_tier):
    with pytest.raises(InvalidRuleSetVersionError):
        RuleSetVersion(
            rule_set_version_id="x",
            claim_type=ClaimType.SECTOR_CLASSIFICATION,
            precedence={SourceType.GOVERNMENT_REGISTRY: bad_tier},
            staleness_threshold=timedelta(days=1),
            effective_from=_BASE_TIME,
        )


def test_rule_set_version_accepts_valid_precedence():
    rule_set = RuleSetVersion(
        rule_set_version_id="x",
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        precedence={SourceType.GOVERNMENT_REGISTRY: 1},
        staleness_threshold=timedelta(days=1),
        effective_from=_BASE_TIME,
    )
    assert rule_set.precedence[SourceType.GOVERNMENT_REGISTRY] == 1


# --- recursive deep immutability -------------------------------------------------------


def test_value_json_top_level_mutation_fails():
    claim = _claim(value_json={"sector": "roofing"})
    with pytest.raises(TypeError):
        claim.value_json["sector"] = "hacked"  # type: ignore[index]


def test_value_json_nested_dict_mutation_fails():
    claim = _claim(value_json={"a": {"b": 1}})
    with pytest.raises(TypeError):
        claim.value_json["a"]["b"] = 2  # type: ignore[index]


def test_value_json_nested_list_becomes_immutable_tuple():
    claim = _claim(value_json={"a": [1, 2, 3]})
    assert claim.value_json["a"] == (1, 2, 3)
    with pytest.raises(AttributeError):
        claim.value_json["a"].append(4)  # type: ignore[attr-defined]


def test_precedence_mutation_fails():
    rule_set = RuleSetVersion(
        rule_set_version_id="x",
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        precedence={SourceType.GOVERNMENT_REGISTRY: 1},
        staleness_threshold=timedelta(days=1),
        effective_from=_BASE_TIME,
    )
    with pytest.raises(TypeError):
        rule_set.precedence[SourceType.GOVERNMENT_REGISTRY] = 99  # type: ignore[index]


def test_original_value_json_dict_mutation_does_not_affect_claim():
    original = {"sector": "roofing"}
    claim = _claim(value_json=original)
    original["sector"] = "changed"
    original["extra"] = "new-key"
    assert claim.value_json["sector"] == "roofing"
    assert "extra" not in claim.value_json


def test_original_precedence_dict_mutation_does_not_affect_rule_set():
    original = {SourceType.GOVERNMENT_REGISTRY: 1}
    rule_set = RuleSetVersion(
        rule_set_version_id="x",
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        precedence=original,
        staleness_threshold=timedelta(days=1),
        effective_from=_BASE_TIME,
    )
    original[SourceType.GOVERNMENT_REGISTRY] = 99
    original[SourceType.AI_INFERENCE] = 2
    assert rule_set.precedence[SourceType.GOVERNMENT_REGISTRY] == 1
    assert SourceType.AI_INFERENCE not in rule_set.precedence


# --- ClassificationClaim construction-time value validation --------------------------


@pytest.mark.parametrize("field", ["source_reliability", "extraction_confidence"])
@pytest.mark.parametrize("bad_value", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_classification_claim_rejects_invalid_confidence_values(field, bad_value):
    with pytest.raises(InvalidClassificationClaimError):
        _claim(**{field: bad_value})


@pytest.mark.parametrize("valid_value", [0.0, 1.0, 0.5, 1, 0])
def test_classification_claim_accepts_boundary_and_integer_confidence_values(
    valid_value,
):
    claim = _claim(source_reliability=valid_value, extraction_confidence=valid_value)
    assert claim.source_reliability == valid_value


@pytest.mark.parametrize("field", ["primary_evidence_content_hash", "idempotency_key"])
@pytest.mark.parametrize("bad_hash", ["", "a" * 63, "A" * 64, "not-a-hash", "g" * 64])
def test_classification_claim_rejects_invalid_hash_fields(field, bad_hash):
    with pytest.raises(InvalidClassificationClaimError):
        _claim(**{field: bad_hash})


@pytest.mark.parametrize("bad_company_id", [0, -1, True, False, 1.5, "1"])
def test_classification_claim_rejects_invalid_company_id(bad_company_id):
    with pytest.raises(InvalidClassificationClaimError):
        _claim(company_id=bad_company_id)


def test_classification_claim_accepts_positive_company_id():
    claim = _claim(company_id=42)
    assert claim.company_id == 42


@pytest.mark.parametrize(
    "field", ["claim_id", "predicate", "extraction_method", "rule_set_version_id"]
)
def test_classification_claim_rejects_empty_identifier_fields(field):
    with pytest.raises(InvalidClassificationClaimError):
        _claim(**{field: ""})


@pytest.mark.parametrize(
    "field", ["observed_at", "effective_at", "extracted_at", "created_at"]
)
def test_classification_claim_rejects_naive_datetime(field):
    with pytest.raises(InvalidClassificationClaimError):
        _claim(**{field: _NAIVE_TIME})


# --- ClaimEvent construction-time value validation ------------------------------------


@pytest.mark.parametrize(
    "field", ["event_id", "claim_id", "actor_id", "rule_set_version_id"]
)
def test_claim_event_rejects_empty_identifier_fields(field):
    with pytest.raises(InvalidClaimEventError):
        _event(**{field: ""})


@pytest.mark.parametrize("field", ["event_at", "created_at"])
def test_claim_event_rejects_naive_datetime(field):
    with pytest.raises(InvalidClaimEventError):
        _event(**{field: _NAIVE_TIME})


# --- RuleSetVersion construction-time value validation --------------------------------


def test_rule_set_version_rejects_empty_id():
    with pytest.raises(InvalidRuleSetVersionError):
        RuleSetVersion(
            rule_set_version_id="",
            claim_type=ClaimType.SECTOR_CLASSIFICATION,
            precedence={SourceType.GOVERNMENT_REGISTRY: 1},
            staleness_threshold=timedelta(days=1),
            effective_from=_BASE_TIME,
        )


def test_rule_set_version_rejects_naive_effective_from():
    with pytest.raises(InvalidRuleSetVersionError):
        RuleSetVersion(
            rule_set_version_id="x",
            claim_type=ClaimType.SECTOR_CLASSIFICATION,
            precedence={SourceType.GOVERNMENT_REGISTRY: 1},
            staleness_threshold=timedelta(days=1),
            effective_from=_NAIVE_TIME,
        )


def test_rule_set_version_rejects_negative_staleness_threshold():
    with pytest.raises(InvalidRuleSetVersionError):
        RuleSetVersion(
            rule_set_version_id="x",
            claim_type=ClaimType.SECTOR_CLASSIFICATION,
            precedence={SourceType.GOVERNMENT_REGISTRY: 1},
            staleness_threshold=timedelta(days=-1),
            effective_from=_BASE_TIME,
        )


def test_rule_set_version_accepts_zero_staleness_threshold():
    rule_set = RuleSetVersion(
        rule_set_version_id="x",
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        precedence={SourceType.GOVERNMENT_REGISTRY: 1},
        staleness_threshold=timedelta(0),
        effective_from=_BASE_TIME,
    )
    assert rule_set.staleness_threshold == timedelta(0)


def test_rule_set_version_rejects_disallowed_source_for_licence_registration():
    with pytest.raises(InvalidRuleSetVersionError):
        RuleSetVersion(
            rule_set_version_id="licence_registration_v1",
            claim_type=ClaimType.LICENCE_REGISTRATION,
            precedence={
                SourceType.GOVERNMENT_REGISTRY: 1,
                SourceType.ASSOCIATION_DIRECTORY: 2,
            },
            staleness_threshold=timedelta(days=1),
            effective_from=_BASE_TIME,
        )


def test_rule_set_version_accepts_only_approved_licence_sources():
    rule_set = RuleSetVersion(
        rule_set_version_id="licence_registration_v1",
        claim_type=ClaimType.LICENCE_REGISTRATION,
        precedence={SourceType.GOVERNMENT_REGISTRY: 1, SourceType.LICENCE_AUTHORITY: 1},
        staleness_threshold=timedelta(days=1),
        effective_from=_BASE_TIME,
    )
    assert set(rule_set.precedence) == {
        SourceType.GOVERNMENT_REGISTRY,
        SourceType.LICENCE_AUTHORITY,
    }


def test_rule_set_version_accepts_full_approved_sector_precedence():
    rule_set = RuleSetVersion(
        rule_set_version_id="sector_classification_v1",
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        precedence=dict(SECTOR_CLASSIFICATION_PRECEDENCE_V1),
        staleness_threshold=timedelta(days=1),
        effective_from=_BASE_TIME,
    )
    assert set(rule_set.precedence) == set(SourceType)


# --- public precedence constants are immutable ----------------------------------------


def test_sector_precedence_constant_is_immutable():
    with pytest.raises(TypeError):
        SECTOR_CLASSIFICATION_PRECEDENCE_V1[SourceType.GOVERNMENT_REGISTRY] = 99  # type: ignore[index]


def test_licence_precedence_constant_is_immutable():
    with pytest.raises(TypeError):
        LICENCE_REGISTRATION_PRECEDENCE_V1[SourceType.GOVERNMENT_REGISTRY] = 99  # type: ignore[index]
