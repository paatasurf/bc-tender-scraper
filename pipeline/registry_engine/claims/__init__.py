"""Classification Claims — pure domain, resolver (PR-A), Gateway, and
consistency audit evaluator (PR-B2).

``domain.py`` / ``canonical.py`` / ``resolver.py`` / ``consistency.py`` are
pure: no database, no SQLAlchemy, no I/O. ``gateway.py`` is the exception --
it is the only sanctioned write path into the migration-029 schema. It
never resolves ``DATABASE_URL`` itself: ``dry_run=True`` accepts a plain
read-only ``Engine``, but every real write (``submit_claim(...,
dry_run=False)`` / ``record_event(...)``) requires a
:class:`ClaimsWriteCapability`, obtainable only via
``gateway_capability.acquire_claims_write_capability()`` -- which is the one
place in this package that runs the existing Class C/D guard (a real human
TTY confirmation for production). See ``gateway.py`` and
``gateway_capability.py`` module docstrings for the full contract.
``consistency.py`` is the pure evaluator behind the Class A audit in
``db.classification_claims_consistency_audit`` /
``scripts/run_claims_consistency_audit.py``.
"""

from pipeline.registry_engine.claims.consistency import (
    ClaimRow,
    EventRow,
    EvidenceRow,
    RuleSetRow,
    evaluate_claims_consistency,
)
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
from pipeline.registry_engine.claims.gateway import (
    EVIDENCE_SOURCES,
    LICENCE_REGISTRATION_PREDICATES,
    LICENCE_STATUS_VALUES,
    SECTOR_CLASSIFICATION_PREDICATES,
    ClaimAlreadyExistsResult,
    ClaimDryRunResult,
    ClaimNotFoundError,
    ClaimsGatewayError,
    ClaimSubmitted,
    ClaimsWriteCapability,
    CompanyNotFoundError,
    CrossScopeRelatedClaimError,
    EvidenceInsertFailedError,
    InvalidClaimTimestampsError,
    InvalidClaimValueError,
    InvalidEvidenceLocatorError,
    InvalidEvidenceSourceError,
    InvalidPredicateForClaimTypeError,
    LicenceSourceNotAllowedError,
    RecordedEvent,
    RelatedClaimNotFoundError,
    RuleSetVersionNotFoundError,
    SubmitClaimOutcome,
    TerminalEventAlreadyExistsError,
    UnauthorizedWriteError,
    record_event,
    submit_claim,
)
from pipeline.registry_engine.claims.gateway_capability import (
    acquire_claims_write_capability,
)
from pipeline.registry_engine.claims.resolver import (
    ClaimsResolutionError,
    IncompatibleRuleSetVersionError,
    MalformedClaimStreamError,
    resolve,
)

__all__ = [
    "ActorType",
    "ClaimAlreadyExistsResult",
    "ClaimDryRunResult",
    "ClaimEvent",
    "ClaimNotFoundError",
    "ClaimRow",
    "ClaimSubmitted",
    "ClaimType",
    "ClaimsDomainError",
    "ClaimsGatewayError",
    "ClaimsResolutionError",
    "ClaimsWriteCapability",
    "ClassificationClaim",
    "CompanyNotFoundError",
    "CrossScopeRelatedClaimError",
    "EVIDENCE_SOURCES",
    "EventRow",
    "EventType",
    "EvidenceInsertFailedError",
    "EvidenceRow",
    "IncompatibleRuleSetVersionError",
    "InvalidClaimEventError",
    "InvalidClaimTimestampsError",
    "InvalidClaimValueError",
    "InvalidClassificationClaimError",
    "InvalidEvidenceLocatorError",
    "InvalidEvidenceSourceError",
    "InvalidHashInputError",
    "InvalidPredicateForClaimTypeError",
    "InvalidRuleSetVersionError",
    "LICENCE_REGISTRATION_ALLOWED_SOURCES",
    "LICENCE_REGISTRATION_PRECEDENCE_V1",
    "LICENCE_REGISTRATION_PREDICATES",
    "LICENCE_STATUS_VALUES",
    "LicenceSourceNotAllowedError",
    "MalformedClaimStreamError",
    "NoBelief",
    "NonCanonicalValueError",
    "RecordedEvent",
    "RelatedClaimNotFoundError",
    "ResolutionStatus",
    "ResolvedBelief",
    "RuleSetRow",
    "RuleSetVersion",
    "RuleSetVersionNotFoundError",
    "SECTOR_CLASSIFICATION_PRECEDENCE_V1",
    "SECTOR_CLASSIFICATION_PREDICATES",
    "SourceType",
    "SubmitClaimOutcome",
    "TerminalEventAlreadyExistsError",
    "UnauthorizedWriteError",
    "acquire_claims_write_capability",
    "canonical_json",
    "compute_claim_idempotency_key",
    "compute_evidence_fingerprint",
    "evaluate_claims_consistency",
    "is_timezone_aware",
    "is_valid_sha256",
    "record_event",
    "resolve",
    "submit_claim",
]
