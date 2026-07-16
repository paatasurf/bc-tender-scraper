"""Deterministic resolution of a Classification Claim ledger to a single
current belief, for one (company_id, claim_type, predicate) key, within a
bitemporal window.

Pure function — no I/O, no database session, no wall-clock reads. Both
``effective_as_of`` (business time) and ``knowledge_as_of`` (system knowledge
time) are explicit caller-supplied inputs. All temporal filtering happens
here, inside the resolver — a caller must never pre-filter ``claims``/
``events`` itself, since a silently mis-filtered input would produce a wrong
answer that looks structurally valid.

Every call also validates that the supplied ``rule_set_version`` is
applicable and that the ``claims``/``events`` streams are internally
consistent (no duplicate IDs, no orphan/self/cross-scope references, at most
one terminal event per claim) before any resolution logic runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pipeline.registry_engine.claims.canonical import canonical_json
from pipeline.registry_engine.claims.domain import (
    ClaimEvent,
    ClaimsDomainError,
    ClaimType,
    ClassificationClaim,
    EventType,
    NoBelief,
    ResolvedBelief,
    RuleSetVersion,
    is_timezone_aware,
)


class ClaimsResolutionError(ClaimsDomainError):
    """Raised when ``resolve()`` is called with structurally invalid
    parameters — currently: a naive (timezone-unaware), or otherwise
    incompatible with the timezone-aware contract, ``effective_as_of`` or
    ``knowledge_as_of``."""


class IncompatibleRuleSetVersionError(ClaimsDomainError):
    """Raised when the supplied ``RuleSetVersion`` is not applicable to this
    ``resolve()`` call (wrong claim_type, or not yet effective)."""


class MalformedClaimStreamError(ClaimsDomainError):
    """Raised when the ``claims``/``events`` input streams violate a
    structural invariant that can only be checked across multiple records
    (duplicate IDs, orphan/self/cross-scope references, more than one
    terminal event on the same claim)."""


def _validate_input_streams(
    claims: Sequence[ClassificationClaim], events: Sequence[ClaimEvent]
) -> None:
    claims_by_id: dict[str, ClassificationClaim] = {}
    for c in claims:
        if c.claim_id in claims_by_id:
            raise MalformedClaimStreamError(
                f"duplicate claim_id in claims stream: {c.claim_id}"
            )
        claims_by_id[c.claim_id] = c

    seen_event_ids: set[str] = set()
    events_per_claim: dict[str, int] = {}
    for e in events:
        if e.event_id in seen_event_ids:
            raise MalformedClaimStreamError(
                f"duplicate event_id in events stream: {e.event_id}"
            )
        seen_event_ids.add(e.event_id)

        if e.claim_id not in claims_by_id:
            raise MalformedClaimStreamError(
                f"event {e.event_id} references unknown claim_id: {e.claim_id}"
            )

        events_per_claim[e.claim_id] = events_per_claim.get(e.claim_id, 0) + 1
        if events_per_claim[e.claim_id] > 1:
            raise MalformedClaimStreamError(
                f"claim {e.claim_id} has more than one terminal event in the events stream"
            )

        if e.event_type == EventType.SUPERSEDED:
            related = claims_by_id.get(e.related_claim_id)
            if related is None:
                raise MalformedClaimStreamError(
                    f"event {e.event_id}: related_claim_id {e.related_claim_id!r} not found in claims stream"
                )
            subject = claims_by_id[e.claim_id]
            if (
                related.company_id != subject.company_id
                or related.claim_type != subject.claim_type
                or related.predicate != subject.predicate
            ):
                raise MalformedClaimStreamError(
                    f"event {e.event_id}: related_claim_id {e.related_claim_id!r} is outside the "
                    "company/claim_type/predicate scope of the superseded claim"
                )


def resolve(
    *,
    company_id: int,
    claim_type: ClaimType,
    predicate: str,
    effective_as_of: datetime,
    knowledge_as_of: datetime,
    claims: Sequence[ClassificationClaim],
    events: Sequence[ClaimEvent],
    rule_set_version: RuleSetVersion,
) -> ResolvedBelief | NoBelief:
    for field_name, value in (
        ("effective_as_of", effective_as_of),
        ("knowledge_as_of", knowledge_as_of),
    ):
        if not is_timezone_aware(value):
            raise ClaimsResolutionError(
                f"{field_name} must be a timezone-aware datetime, got {value!r}"
            )

    if rule_set_version.claim_type != claim_type:
        raise IncompatibleRuleSetVersionError(
            f"rule_set_version.claim_type={rule_set_version.claim_type!r} does not match "
            f"requested claim_type={claim_type!r}"
        )
    if rule_set_version.effective_from > effective_as_of:
        raise IncompatibleRuleSetVersionError(
            f"rule_set_version {rule_set_version.rule_set_version_id!r} is not yet effective "
            f"(effective_from={rule_set_version.effective_from!r} > effective_as_of={effective_as_of!r})"
        )

    _validate_input_streams(claims, events)

    candidate_claims = [
        c
        for c in claims
        if c.company_id == company_id
        and c.claim_type == claim_type
        and c.predicate == predicate
        and c.effective_at <= effective_as_of
        and c.extracted_at <= knowledge_as_of
    ]
    if not candidate_claims:
        return NoBelief()

    candidate_claim_ids = {c.claim_id for c in candidate_claims}
    candidate_events = [
        e
        for e in events
        if e.claim_id in candidate_claim_ids
        and e.event_at <= effective_as_of
        and e.created_at <= knowledge_as_of
    ]

    # At most one event per claim is guaranteed by _validate_input_streams,
    # so this is a direct lookup, not a fold over multiple candidates.
    terminal_event_by_claim: dict[str, ClaimEvent] = {
        e.claim_id: e for e in candidate_events
    }

    claims_by_id = {c.claim_id: c for c in candidate_claims}

    # Human adjudication wins unconditionally, outside precedence. Latest by
    # (event_at, event_id) among all adjudicated claims for this key.
    adjudications = [
        (claims_by_id[claim_id], event)
        for claim_id, event in terminal_event_by_claim.items()
        if event.event_type == EventType.ADJUDICATED
    ]
    if adjudications:
        winner, _event = max(
            adjudications, key=lambda pair: (pair[1].event_at, pair[1].event_id)
        )
        return ResolvedBelief(
            winning_claim_id=winner.claim_id,
            resolution_status="adjudicated",
            resolution_confidence=min(
                winner.source_reliability, winner.extraction_confidence
            ),
            competing_claim_count=len(candidate_claims) - 1,
            effective_as_of=effective_as_of,
            knowledge_as_of=knowledge_as_of,
        )

    # No adjudication: any remaining terminal event is rejected/superseded —
    # drop those claims.
    effective_claims = [
        c for c in candidate_claims if c.claim_id not in terminal_event_by_claim
    ]

    applicable = [
        c for c in effective_claims if c.source_type in rule_set_version.precedence
    ]
    if not applicable:
        return NoBelief()

    best_tier = min(rule_set_version.precedence[c.source_type] for c in applicable)
    top_tier = [
        c for c in applicable if rule_set_version.precedence[c.source_type] == best_tier
    ]

    winner = sorted(
        top_tier,
        key=lambda c: (
            c.effective_at,
            c.observed_at,
            c.extraction_confidence,
            c.source_reliability,
            c.claim_id,
        ),
        reverse=True,
    )[0]

    winner_canonical = canonical_json(winner.value_json)
    same_tier_disagree = any(
        canonical_json(c.value_json) != winner_canonical
        for c in top_tier
        if c.claim_id != winner.claim_id
    )
    is_stale = (
        effective_as_of - winner.observed_at
    ) > rule_set_version.staleness_threshold

    if same_tier_disagree:
        status = "disputed"
    elif is_stale:
        status = "stale"
    else:
        status = "resolved"

    return ResolvedBelief(
        winning_claim_id=winner.claim_id,
        resolution_status=status,
        resolution_confidence=min(
            winner.source_reliability, winner.extraction_confidence
        ),
        competing_claim_count=len(applicable) - 1,
        effective_as_of=effective_as_of,
        knowledge_as_of=knowledge_as_of,
    )
