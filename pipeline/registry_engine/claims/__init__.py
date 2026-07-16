"""Classification Claims — pure domain and resolver (PR-A).

No database, no SQLAlchemy, no I/O. This package only defines immutable data
shapes and a pure resolution function; it does not persist or query anything.
See ``domain.py`` / ``canonical.py`` / ``resolver.py`` for the individual
contracts.
"""

from pipeline.registry_engine.claims.canonical import (
    InvalidHashInputError,
    NonCanonicalValueError,
    canonical_json,
    compute_claim_idempotency_key,
    compute_evidence_fingerprint,
    is_valid_sha256,
)
from pipeline.registry_engine.claims.domain import (
    ActorType,
    ClaimEvent,
    ClaimsDomainError,
    ClaimType,
    ClassificationClaim,
    EventType,
    InvalidClaimEventError,
    InvalidClassificationClaimError,
    InvalidRuleSetVersionError,
    LICENCE_REGISTRATION_ALLOWED_SOURCES,
    LICENCE_REGISTRATION_PRECEDENCE_V1,
    NoBelief,
    ResolutionStatus,
    ResolvedBelief,
    RuleSetVersion,
    SECTOR_CLASSIFICATION_PRECEDENCE_V1,
    SourceType,
    is_timezone_aware,
)
from pipeline.registry_engine.claims.resolver import (
    ClaimsResolutionError,
    IncompatibleRuleSetVersionError,
    MalformedClaimStreamError,
    resolve,
)

__all__ = [
    "ActorType",
    "ClaimEvent",
    "ClaimType",
    "ClaimsDomainError",
    "ClaimsResolutionError",
    "ClassificationClaim",
    "EventType",
    "IncompatibleRuleSetVersionError",
    "InvalidClaimEventError",
    "InvalidClassificationClaimError",
    "InvalidHashInputError",
    "InvalidRuleSetVersionError",
    "LICENCE_REGISTRATION_ALLOWED_SOURCES",
    "LICENCE_REGISTRATION_PRECEDENCE_V1",
    "MalformedClaimStreamError",
    "NoBelief",
    "NonCanonicalValueError",
    "ResolutionStatus",
    "ResolvedBelief",
    "RuleSetVersion",
    "SECTOR_CLASSIFICATION_PRECEDENCE_V1",
    "SourceType",
    "canonical_json",
    "compute_claim_idempotency_key",
    "compute_evidence_fingerprint",
    "is_timezone_aware",
    "is_valid_sha256",
    "resolve",
]
