"""Registry Engine Stage 1 (RE1) — decide().

Parses a raw identity string with the canonical parser (Spec 007's
pipeline.identity_parser.parse_identity — ADR-8 of the unified Registry
Engine architecture) and shadow-evaluates it against the existing
CompanyResolver without creating or mutating anything.

Read-only: resolver.resolve(..., create_if_missing=False) never reaches
CompanyResolver._create_company, so no row is created, updated, or deleted
by this function or its callees.

Stage 1 scope: MATCH / CREATE / REJECT only. MERGE requires two existing
companies plus merge-candidate detection, which belongs to a later
enforcement stage (Stage 4) — nothing here blocks adding it then.
"""

from __future__ import annotations

from pipeline.company_resolution import RESOLUTION_STATUS_PERSON_SKIP, CompanyResolver
from pipeline.identity_parser import RelationshipType, parse_identity
from pipeline.registry_engine.constants import (
    DECISION_CREATE,
    DECISION_MATCH,
    DECISION_REJECT,
    REGISTRY_CONFIDENCE_LOW,
    REGISTRY_CONFIDENCE_MEDIUM,
    REGISTRY_CONFIDENCE_UNVERIFIED,
    REJECT_REASON_EMPTY,
    REJECT_REASON_PERSON,
)
from pipeline.registry_engine.domain import EngineDecision


def _to_registry_confidence(confidence: float) -> str:
    """Map CompanyResolver's raw float to a Registry Confidence label.

    Capped at "medium": decide() has performed no OrgBook/ODB cross-check,
    so "high"/"verified" (which require registry corroboration per spec.md
    Section 7.3) would overclaim certainty.
    """
    if confidence >= 0.95:
        return REGISTRY_CONFIDENCE_MEDIUM
    if confidence >= 0.80:
        return REGISTRY_CONFIDENCE_LOW
    return REGISTRY_CONFIDENCE_UNVERIFIED


def decide(
    raw_identity: str,
    *,
    resolver: CompanyResolver,
    source: str,
    city: str = "",
    province: str = "BC",
) -> EngineDecision:
    """Shadow-decide MATCH / CREATE / REJECT for one raw identity string."""
    parsed = parse_identity(raw_identity)

    if not parsed.raw_identity:
        return EngineDecision(decision=DECISION_REJECT, parsed_identity=parsed, reject_reason=REJECT_REASON_EMPTY)

    if parsed.relationship_type == RelationshipType.PLAIN_PERSON or parsed.business_name is None:
        return EngineDecision(
            decision=DECISION_REJECT,
            parsed_identity=parsed,
            reject_reason=REJECT_REASON_PERSON,
            metadata={"person_name": parsed.person_name},
        )

    target = parsed.resolution_target()

    resolution = resolver.resolve(
        target,
        source=source,
        city=city,
        province=province,
        create_if_missing=False,
    )

    if resolution.status == RESOLUTION_STATUS_PERSON_SKIP:
        return EngineDecision(
            decision=DECISION_REJECT,
            parsed_identity=parsed,
            reject_reason=REJECT_REASON_PERSON,
            method=resolution.method,
        )

    registry_confidence = _to_registry_confidence(resolution.confidence)

    if resolution.company_id is not None:
        return EngineDecision(
            decision=DECISION_MATCH,
            parsed_identity=parsed,
            company_id=resolution.company_id,
            registry_confidence=registry_confidence,
            method=resolution.method,
            metadata={"canonical_key": resolution.canonical_key},
        )

    # No existing candidate: legacy CompanyResolver would create here.
    # Stage 1 only shadow-logs this as a CREATE signal — it does not create.
    return EngineDecision(
        decision=DECISION_CREATE,
        parsed_identity=parsed,
        company_id=None,
        registry_confidence=registry_confidence,
        method=resolution.method,
        metadata={"canonical_key": resolution.canonical_key, "display_name": resolution.display_name},
    )
