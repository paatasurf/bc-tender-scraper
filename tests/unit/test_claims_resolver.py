"""Pure unit tests for pipeline.registry_engine.claims.resolver.resolve().

No database, no I/O — every test constructs plain dataclasses in memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.registry_engine.claims.domain import (
    ActorType,
    ClaimEvent,
    ClaimType,
    ClassificationClaim,
    EventType,
    LICENCE_REGISTRATION_PRECEDENCE_V1,
    NoBelief,
    ResolvedBelief,
    RuleSetVersion,
    SECTOR_CLASSIFICATION_PRECEDENCE_V1,
    SourceType,
)
from pipeline.registry_engine.claims.resolver import (
    ClaimsResolutionError,
    IncompatibleRuleSetVersionError,
    MalformedClaimStreamError,
    resolve,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
FAR_FUTURE = BASE_TIME + timedelta(days=365)
NAIVE_TIME = datetime(2026, 6, 1)  # deliberately no tzinfo


def _uid() -> str:
    return str(uuid.uuid4())


def _claim(**overrides) -> ClassificationClaim:
    base = dict(
        claim_id=_uid(),
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
        observed_at=BASE_TIME,
        effective_at=BASE_TIME,
        extracted_at=BASE_TIME,
        idempotency_key="b" * 64,
        created_at=BASE_TIME,
    )
    base.update(overrides)
    return ClassificationClaim(**base)


def _event(**overrides) -> ClaimEvent:
    base = dict(
        event_id=_uid(),
        claim_id=_uid(),
        event_type=EventType.REJECTED,
        related_claim_id=None,
        actor_type=ActorType.SYSTEM,
        actor_id="test",
        rationale=None,
        rule_set_version_id="sector_classification_v1",
        event_at=BASE_TIME,
        created_at=BASE_TIME,
    )
    base.update(overrides)
    return ClaimEvent(**base)


def _rule_set(**overrides) -> RuleSetVersion:
    base = dict(
        rule_set_version_id="sector_classification_v1",
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        precedence=dict(SECTOR_CLASSIFICATION_PRECEDENCE_V1),
        # Deliberately larger than FAR_FUTURE - BASE_TIME so tests not
        # exercising staleness (which use the FAR_FUTURE default effective_as_of)
        # don't spuriously trip it. Staleness-specific tests override this.
        staleness_threshold=timedelta(days=3650),
        effective_from=BASE_TIME,
    )
    base.update(overrides)
    return RuleSetVersion(**base)


def _resolve(
    claims,
    events,
    rule_set=None,
    effective_as_of=FAR_FUTURE,
    knowledge_as_of=FAR_FUTURE,
    **kw,
):
    return resolve(
        company_id=kw.get("company_id", 1),
        claim_type=kw.get("claim_type", ClaimType.SECTOR_CLASSIFICATION),
        predicate=kw.get("predicate", "dominant_sector"),
        effective_as_of=effective_as_of,
        knowledge_as_of=knowledge_as_of,
        claims=claims,
        events=events,
        rule_set_version=rule_set or _rule_set(),
    )


# --- every precedence tier -----------------------------------------------------


@pytest.mark.parametrize("source_type", list(SourceType))
def test_every_sector_source_type_resolves_when_alone(source_type):
    claim = _claim(source_type=source_type)
    result = _resolve([claim], [])
    assert isinstance(result, ResolvedBelief)
    assert result.winning_claim_id == claim.claim_id
    assert result.resolution_status == "resolved"


def test_best_sector_tier_wins_among_all_seven():
    claims = [
        _claim(source_type=st, value_json={"sector": st.value}) for st in SourceType
    ]
    result = _resolve(claims, [])
    winner = next(c for c in claims if c.claim_id == result.winning_claim_id)
    assert winner.source_type == SourceType.LICENCE_AUTHORITY


def test_licence_registration_tie_between_government_and_licence_authority():
    rule_set = _rule_set(
        rule_set_version_id="licence_registration_v1",
        claim_type=ClaimType.LICENCE_REGISTRATION,
        precedence=dict(LICENCE_REGISTRATION_PRECEDENCE_V1),
    )
    gov = _claim(
        claim_type=ClaimType.LICENCE_REGISTRATION,
        predicate="licence_identifier",
        source_type=SourceType.GOVERNMENT_REGISTRY,
        effective_at=BASE_TIME,
        value_json={"licence_identifier": "AAA"},
    )
    lic = _claim(
        claim_type=ClaimType.LICENCE_REGISTRATION,
        predicate="licence_identifier",
        source_type=SourceType.LICENCE_AUTHORITY,
        effective_at=BASE_TIME + timedelta(days=1),  # more recent -> tie-break winner
        value_json={"licence_identifier": "BBB"},
    )
    result = _resolve(
        [gov, lic],
        [],
        rule_set=rule_set,
        claim_type=ClaimType.LICENCE_REGISTRATION,
        predicate="licence_identifier",
    )
    assert result.winning_claim_id == lic.claim_id


def test_licence_registration_rejects_association_directory_as_inapplicable():
    rule_set = _rule_set(
        rule_set_version_id="licence_registration_v1",
        claim_type=ClaimType.LICENCE_REGISTRATION,
        precedence=dict(LICENCE_REGISTRATION_PRECEDENCE_V1),
    )
    claim = _claim(
        claim_type=ClaimType.LICENCE_REGISTRATION,
        predicate="licence_identifier",
        source_type=SourceType.ASSOCIATION_DIRECTORY,
    )
    result = _resolve(
        [claim],
        [],
        rule_set=rule_set,
        claim_type=ClaimType.LICENCE_REGISTRATION,
        predicate="licence_identifier",
    )
    assert isinstance(result, NoBelief)


# --- full deterministic tie-break sequence --------------------------------------


def _tie_pair(**overrides_b):
    a = _claim(
        claim_id="claim-a",
        effective_at=BASE_TIME,
        observed_at=BASE_TIME,
        extraction_confidence=0.5,
        source_reliability=0.5,
    )
    b_kwargs = dict(
        claim_id="claim-b",
        effective_at=BASE_TIME,
        observed_at=BASE_TIME,
        extraction_confidence=0.5,
        source_reliability=0.5,
    )
    b_kwargs.update(overrides_b)
    b = _claim(**b_kwargs)
    return a, b


def test_tie_break_level_1_effective_at_decides():
    a, b = _tie_pair(effective_at=BASE_TIME + timedelta(days=1))
    result = _resolve([a, b], [])
    assert result.winning_claim_id == "claim-b"


def test_tie_break_level_2_observed_at_decides_when_effective_at_ties():
    a, b = _tie_pair(observed_at=BASE_TIME + timedelta(hours=1))
    result = _resolve([a, b], [])
    assert result.winning_claim_id == "claim-b"


def test_tie_break_level_3_extraction_confidence_decides():
    a, b = _tie_pair(extraction_confidence=0.9)
    result = _resolve([a, b], [])
    assert result.winning_claim_id == "claim-b"


def test_tie_break_level_4_source_reliability_decides():
    a, b = _tie_pair(source_reliability=0.9)
    result = _resolve([a, b], [])
    assert result.winning_claim_id == "claim-b"


def test_tie_break_level_5_claim_id_is_final_tiebreak():
    a, b = _tie_pair()  # identical on every prior level
    result = _resolve([a, b], [])
    assert (
        result.winning_claim_id == "claim-b"
    )  # "claim-b" > "claim-a" lexicographically


# --- historical / bitemporal filtering ------------------------------------------


def test_future_effective_claim_excluded():
    claim = _claim(effective_at=BASE_TIME + timedelta(days=10))
    result = _resolve(
        [claim], [], effective_as_of=BASE_TIME, knowledge_as_of=FAR_FUTURE
    )
    assert isinstance(result, NoBelief)


def test_late_extracted_claim_excluded_by_knowledge_as_of():
    claim = _claim(effective_at=BASE_TIME, extracted_at=BASE_TIME + timedelta(days=10))
    result = _resolve(
        [claim], [], effective_as_of=FAR_FUTURE, knowledge_as_of=BASE_TIME
    )
    assert isinstance(result, NoBelief)


def test_claim_visible_once_both_effective_and_knowledge_thresholds_pass():
    claim = _claim(effective_at=BASE_TIME, extracted_at=BASE_TIME)
    result = _resolve([claim], [], effective_as_of=BASE_TIME, knowledge_as_of=BASE_TIME)
    assert isinstance(result, ResolvedBelief)


def test_future_event_at_excluded_leaves_claim_active():
    claim = _claim()
    future_reject = _event(
        claim_id=claim.claim_id,
        event_type=EventType.REJECTED,
        event_at=BASE_TIME + timedelta(days=10),
    )
    result = _resolve(
        [claim], [future_reject], effective_as_of=BASE_TIME, knowledge_as_of=FAR_FUTURE
    )
    assert isinstance(result, ResolvedBelief)
    assert result.winning_claim_id == claim.claim_id


def test_future_created_event_excluded_by_knowledge_as_of():
    claim = _claim()
    late_known_reject = _event(
        claim_id=claim.claim_id,
        event_type=EventType.REJECTED,
        event_at=BASE_TIME,
        created_at=BASE_TIME + timedelta(days=10),
    )
    result = _resolve(
        [claim],
        [late_known_reject],
        effective_as_of=FAR_FUTURE,
        knowledge_as_of=BASE_TIME,
    )
    assert isinstance(result, ResolvedBelief)
    assert result.winning_claim_id == claim.claim_id


# --- terminal disposition -------------------------------------------------------


@pytest.mark.parametrize("event_type", [EventType.REJECTED, EventType.SUPERSEDED])
def test_terminal_disposed_claim_excluded(event_type):
    claim = _claim()
    other = _claim()
    related = other.claim_id if event_type == EventType.SUPERSEDED else None
    ev = _event(
        claim_id=claim.claim_id, event_type=event_type, related_claim_id=related
    )
    result = _resolve([claim, other], [ev])
    assert result.winning_claim_id == other.claim_id


def test_all_claims_terminally_disposed_yields_no_belief():
    claim = _claim()
    ev = _event(claim_id=claim.claim_id, event_type=EventType.REJECTED)
    result = _resolve([claim], [ev])
    assert isinstance(result, NoBelief)


# --- human adjudication ----------------------------------------------------------


def test_human_adjudication_overrides_precedence():
    high_tier = _claim(source_type=SourceType.LICENCE_AUTHORITY)  # tier 1, best
    low_tier = _claim(source_type=SourceType.AI_INFERENCE)  # tier 7, worst
    adjudication = _event(
        claim_id=low_tier.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
        actor_id="reviewer-1",
    )
    result = _resolve([high_tier, low_tier], [adjudication])
    assert result.winning_claim_id == low_tier.claim_id
    assert result.resolution_status == "adjudicated"


def test_multiple_adjudications_latest_event_at_wins():
    claim_a = _claim()
    claim_b = _claim()
    ev_a = _event(
        claim_id=claim_a.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
        event_at=BASE_TIME,
    )
    ev_b = _event(
        claim_id=claim_b.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
        event_at=BASE_TIME + timedelta(hours=1),
    )
    result = _resolve([claim_a, claim_b], [ev_a, ev_b])
    assert result.winning_claim_id == claim_b.claim_id


def test_multiple_adjudications_same_event_at_tie_broken_by_event_id():
    claim_a = _claim()
    claim_b = _claim()
    ev_a = _event(
        claim_id=claim_a.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
        event_at=BASE_TIME,
        event_id="event-a",
    )
    ev_b = _event(
        claim_id=claim_b.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
        event_at=BASE_TIME,
        event_id="event-b",
    )
    result = _resolve([claim_a, claim_b], [ev_a, ev_b])
    assert result.winning_claim_id == claim_b.claim_id  # "event-b" > "event-a"


def test_adjudicated_confidence_also_uses_min():
    claim = _claim(source_reliability=0.3, extraction_confidence=0.8)
    ev = _event(
        claim_id=claim.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
    )
    result = _resolve([claim], [ev])
    assert result.resolution_confidence == 0.3


# --- disputed / agreement ---------------------------------------------------------


def test_disputed_when_same_tier_values_disagree():
    a = _claim(claim_id="claim-a", value_json={"sector": "roofing"})
    b = _claim(claim_id="claim-b", value_json={"sector": "electrical"})
    result = _resolve([a, b], [])
    assert result.resolution_status == "disputed"


def test_not_disputed_when_same_tier_values_agree_by_canonical_json():
    a = _claim(claim_id="claim-a", value_json={"sector": "roofing", "note": "x"})
    b = _claim(claim_id="claim-b", value_json={"note": "x", "sector": "roofing"})
    result = _resolve([a, b], [])
    assert result.resolution_status == "resolved"


def test_not_disputed_when_lower_tier_disagrees():
    winner = _claim(
        claim_id="claim-a",
        source_type=SourceType.LICENCE_AUTHORITY,
        value_json={"sector": "roofing"},
    )
    loser = _claim(
        claim_id="claim-b",
        source_type=SourceType.AI_INFERENCE,
        value_json={"sector": "electrical"},
    )
    result = _resolve([winner, loser], [])
    assert result.winning_claim_id == "claim-a"
    assert result.resolution_status == "resolved"


# --- staleness ---------------------------------------------------------------------


def test_stale_when_past_threshold_based_on_observed_at():
    rule_set = _rule_set(staleness_threshold=timedelta(days=30))
    claim = _claim(observed_at=BASE_TIME, effective_at=BASE_TIME)
    result = _resolve(
        [claim],
        [],
        rule_set=rule_set,
        effective_as_of=BASE_TIME + timedelta(days=31),
        knowledge_as_of=FAR_FUTURE,
    )
    assert result.resolution_status == "stale"


def test_not_stale_within_threshold():
    rule_set = _rule_set(staleness_threshold=timedelta(days=30))
    claim = _claim(observed_at=BASE_TIME, effective_at=BASE_TIME)
    result = _resolve(
        [claim],
        [],
        rule_set=rule_set,
        effective_as_of=BASE_TIME + timedelta(days=29),
        knowledge_as_of=FAR_FUTURE,
    )
    assert result.resolution_status == "resolved"


def test_exact_staleness_threshold_boundary_is_not_stale():
    # spec: effective_as_of - observed_at > threshold => stale. Exactly AT the
    # threshold (not >) must NOT be stale.
    rule_set = _rule_set(staleness_threshold=timedelta(days=30))
    claim = _claim(observed_at=BASE_TIME, effective_at=BASE_TIME)
    result = _resolve(
        [claim],
        [],
        rule_set=rule_set,
        effective_as_of=BASE_TIME + timedelta(days=30),
        knowledge_as_of=FAR_FUTURE,
    )
    assert result.resolution_status == "resolved"


def test_one_unit_past_threshold_boundary_is_stale():
    rule_set = _rule_set(staleness_threshold=timedelta(days=30))
    claim = _claim(observed_at=BASE_TIME, effective_at=BASE_TIME)
    result = _resolve(
        [claim],
        [],
        rule_set=rule_set,
        effective_as_of=BASE_TIME + timedelta(days=30, seconds=1),
        knowledge_as_of=FAR_FUTURE,
    )
    assert result.resolution_status == "stale"


def test_adjudicated_claims_are_never_marked_stale():
    rule_set = _rule_set(staleness_threshold=timedelta(days=1))
    claim = _claim(observed_at=BASE_TIME, effective_at=BASE_TIME)
    ev = _event(
        claim_id=claim.claim_id,
        event_type=EventType.ADJUDICATED,
        actor_type=ActorType.HUMAN,
    )
    result = _resolve(
        [claim],
        [ev],
        rule_set=rule_set,
        effective_as_of=BASE_TIME + timedelta(days=365),
        knowledge_as_of=FAR_FUTURE,
    )
    assert result.resolution_status == "adjudicated"


# --- confidence = min() -------------------------------------------------------------


@pytest.mark.parametrize(
    "source_reliability,extraction_confidence,expected",
    [(0.9, 0.6, 0.6), (0.4, 0.95, 0.4), (0.7, 0.7, 0.7), (1.0, 0.0, 0.0)],
)
def test_resolution_confidence_is_min(
    source_reliability, extraction_confidence, expected
):
    claim = _claim(
        source_reliability=source_reliability,
        extraction_confidence=extraction_confidence,
    )
    result = _resolve([claim], [])
    assert result.resolution_confidence == expected


# --- no applicable claims -> NoBelief -----------------------------------------------


def test_no_belief_when_no_claims_at_all():
    result = _resolve([], [])
    assert isinstance(result, NoBelief)


def test_no_belief_when_source_type_not_in_precedence():
    rule_set = _rule_set(precedence={SourceType.GOVERNMENT_REGISTRY: 1})
    claim = _claim(source_type=SourceType.AI_INFERENCE)
    result = _resolve([claim], [], rule_set=rule_set)
    assert isinstance(result, NoBelief)


def test_no_belief_when_predicate_mismatch():
    claim = _claim(predicate="primary_trade")
    result = _resolve([claim], [], predicate="dominant_sector")
    assert isinstance(result, NoBelief)


def test_no_belief_when_company_id_mismatch():
    claim = _claim(company_id=2)
    result = _resolve([claim], [], company_id=1)
    assert isinstance(result, NoBelief)


def test_no_belief_when_claim_type_mismatch():
    claim = _claim(
        claim_type=ClaimType.LICENCE_REGISTRATION, predicate="licence_identifier"
    )
    result = _resolve(
        [claim],
        [],
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        predicate="dominant_sector",
    )
    assert isinstance(result, NoBelief)


# --- purity: no mutation, fully deterministic ----------------------------------------


def test_input_collections_can_be_immutable_tuples():
    """Passing tuples (not lists) proves the resolver never calls a list-mutating
    method (.append/.sort/etc.) on its inputs — it would raise AttributeError
    immediately if it tried."""
    claim = _claim()
    ev = _event(claim_id=claim.claim_id, event_type=EventType.REJECTED)
    result = resolve(
        company_id=1,
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        predicate="dominant_sector",
        effective_as_of=FAR_FUTURE,
        knowledge_as_of=FAR_FUTURE,
        claims=(claim,),
        events=(ev,),
        rule_set_version=_rule_set(),
    )
    assert isinstance(result, NoBelief)


def test_input_lists_not_mutated_in_place():
    claim = _claim()
    ev = _event(claim_id=claim.claim_id, event_type=EventType.REJECTED)
    claims = [claim]
    events = [ev]
    claims_snapshot = list(claims)
    events_snapshot = list(events)
    _resolve(claims, events)
    assert claims == claims_snapshot
    assert events == events_snapshot


def test_repeated_resolution_is_identical():
    claim = _claim()
    kwargs = dict(
        company_id=1,
        claim_type=ClaimType.SECTOR_CLASSIFICATION,
        predicate="dominant_sector",
        effective_as_of=BASE_TIME,
        knowledge_as_of=BASE_TIME,
        claims=[claim],
        events=[],
        rule_set_version=_rule_set(),
    )
    first = resolve(**kwargs)
    second = resolve(**kwargs)
    assert first == second


# --- rule set version compatibility ------------------------------------------------


def test_resolve_rejects_wrong_claim_type_rule_set():
    claim = _claim()
    licence_rule_set = _rule_set(
        rule_set_version_id="licence_registration_v1",
        claim_type=ClaimType.LICENCE_REGISTRATION,
        precedence=dict(LICENCE_REGISTRATION_PRECEDENCE_V1),
    )
    with pytest.raises(IncompatibleRuleSetVersionError):
        _resolve([claim], [], rule_set=licence_rule_set)


def test_resolve_rejects_future_rule_set():
    claim = _claim()
    future_rule_set = _rule_set(effective_from=FAR_FUTURE + timedelta(days=1))
    with pytest.raises(IncompatibleRuleSetVersionError):
        _resolve([claim], [], rule_set=future_rule_set, effective_as_of=FAR_FUTURE)


def test_resolve_accepts_rule_set_effective_exactly_at_effective_as_of():
    claim = _claim()
    rule_set = _rule_set(effective_from=BASE_TIME)
    result = _resolve(
        [claim],
        [],
        rule_set=rule_set,
        effective_as_of=BASE_TIME,
        knowledge_as_of=BASE_TIME,
    )
    assert isinstance(result, ResolvedBelief)


# --- malformed claim/event stream validation ----------------------------------------


def test_resolve_rejects_duplicate_claim_id():
    a = _claim(claim_id="dup")
    b = _claim(claim_id="dup")
    with pytest.raises(MalformedClaimStreamError):
        _resolve([a, b], [])


def test_resolve_rejects_duplicate_event_id():
    claim = _claim()
    other = _claim()
    ev1 = _event(
        claim_id=claim.claim_id, event_id="dup-event", event_type=EventType.REJECTED
    )
    ev2 = _event(
        claim_id=other.claim_id, event_id="dup-event", event_type=EventType.REJECTED
    )
    with pytest.raises(MalformedClaimStreamError):
        _resolve([claim, other], [ev1, ev2])


def test_resolve_rejects_two_events_on_same_claim():
    claim = _claim()
    other = _claim()
    ev1 = _event(claim_id=claim.claim_id, event_type=EventType.REJECTED)
    ev2 = _event(
        claim_id=claim.claim_id,
        event_type=EventType.SUPERSEDED,
        related_claim_id=other.claim_id,
    )
    with pytest.raises(MalformedClaimStreamError):
        _resolve([claim, other], [ev1, ev2])


def test_resolve_rejects_event_for_unknown_claim_id():
    ev = _event(claim_id="does-not-exist", event_type=EventType.REJECTED)
    with pytest.raises(MalformedClaimStreamError):
        _resolve([], [ev])


def test_resolve_rejects_superseded_event_with_unknown_related_claim_id():
    claim = _claim()
    ev = _event(
        claim_id=claim.claim_id,
        event_type=EventType.SUPERSEDED,
        related_claim_id="does-not-exist",
    )
    with pytest.raises(MalformedClaimStreamError):
        _resolve([claim], [ev])


def test_resolve_rejects_cross_scope_related_claim_id():
    claim = _claim(company_id=1)
    other_scope_claim = _claim(company_id=2)  # different company -> cross-scope
    ev = _event(
        claim_id=claim.claim_id,
        event_type=EventType.SUPERSEDED,
        related_claim_id=other_scope_claim.claim_id,
    )
    with pytest.raises(MalformedClaimStreamError):
        _resolve([claim, other_scope_claim], [ev])


def test_resolve_accepts_superseded_event_with_in_scope_related_claim_id():
    claim = _claim()
    other = _claim()  # same company_id/claim_type/predicate defaults -> in scope
    ev = _event(
        claim_id=claim.claim_id,
        event_type=EventType.SUPERSEDED,
        related_claim_id=other.claim_id,
    )
    result = _resolve([claim, other], [ev])
    assert result.winning_claim_id == other.claim_id


# --- resolve() requires timezone-aware effective_as_of / knowledge_as_of -------------


@pytest.mark.parametrize("field", ["effective_as_of", "knowledge_as_of"])
def test_resolve_rejects_naive_resolver_input(field):
    claim = _claim()
    with pytest.raises(ClaimsResolutionError):
        _resolve([claim], [], **{field: NAIVE_TIME})


def test_resolve_rejects_naive_effective_as_of_before_touching_rule_set_or_streams():
    """The timezone check must run before any comparison against
    rule_set_version.effective_from (which is itself always tz-aware) — a
    naive/aware comparison would otherwise raise a generic, unhelpful
    TypeError instead of a clear ClaimsResolutionError."""
    claim = _claim()
    with pytest.raises(ClaimsResolutionError):
        _resolve([claim], [], effective_as_of=NAIVE_TIME, knowledge_as_of=NAIVE_TIME)


def test_resolve_accepts_timezone_aware_inputs_in_a_non_utc_offset():
    from datetime import timezone as _timezone

    other_offset = _timezone(timedelta(hours=-5))
    claim = _claim()
    result = _resolve(
        [claim],
        [],
        effective_as_of=FAR_FUTURE.astimezone(other_offset),
        knowledge_as_of=FAR_FUTURE.astimezone(other_offset),
    )
    assert isinstance(result, ResolvedBelief)
