"""Pure, immutable domain types for the Classification Claims ledger (V1).

No database session, no SQLAlchemy, no I/O. These dataclasses describe the
shape of a claim / event / rule set / resolution outcome — nothing here
persists or queries anything.

V1 claim types: ``sector_classification``, ``licence_registration``.
V1 events: ``superseded``, ``rejected``, ``adjudicated`` — every event type is
terminal; there is no non-terminal event in this version.

``value_json`` and ``precedence`` are deep-frozen at construction time
(recursively, via ``MappingProxyType``/``tuple``) so a caller can never mutate
a claim or rule set after the fact, including through a nested dict/list
reference the caller still holds. Every dataclass also validates its own
fields at construction time (non-empty identifiers, finite confidence values
in [0, 1], lowercase SHA-256 hashes, timezone-aware timestamps) — a
structurally invalid record cannot be constructed at all.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

from pipeline.registry_engine.claims.canonical import is_valid_sha256


class ClaimType(str, Enum):
    SECTOR_CLASSIFICATION = "sector_classification"
    LICENCE_REGISTRATION = "licence_registration"


class SourceType(str, Enum):
    GOVERNMENT_REGISTRY = "government_registry"
    LICENCE_AUTHORITY = "licence_authority"
    ASSOCIATION_DIRECTORY = "association_directory"
    OFFICIAL_WEBSITE = "official_website"
    GOOGLE_BUSINESS_PROFILE = "google_business_profile"
    ACTIVITY_DERIVED = "activity_derived"
    AI_INFERENCE = "ai_inference"


class EventType(str, Enum):
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    ADJUDICATED = "adjudicated"


class ActorType(str, Enum):
    SYSTEM = "system"
    HUMAN = "human"


ResolutionStatus = Literal["resolved", "disputed", "stale", "adjudicated"]

# Approved precedence constants (Codex-approved ranking). Lower number = higher
# precedence. Human adjudication is outside this table entirely — it is
# resolved before precedence is ever consulted (see resolver.resolve()).
# Immutable (MappingProxyType), not a plain dict — a caller cannot mutate the
# shared module-level constant.
SECTOR_CLASSIFICATION_PRECEDENCE_V1: Mapping[SourceType, int] = MappingProxyType(
    {
        SourceType.LICENCE_AUTHORITY: 1,
        SourceType.ASSOCIATION_DIRECTORY: 2,
        SourceType.GOVERNMENT_REGISTRY: 3,
        SourceType.OFFICIAL_WEBSITE: 4,
        SourceType.GOOGLE_BUSINESS_PROFILE: 5,
        SourceType.ACTIVITY_DERIVED: 6,
        SourceType.AI_INFERENCE: 7,
    }
)

LICENCE_REGISTRATION_PRECEDENCE_V1: Mapping[SourceType, int] = MappingProxyType(
    {
        SourceType.GOVERNMENT_REGISTRY: 1,
        SourceType.LICENCE_AUTHORITY: 1,
    }
)

LICENCE_REGISTRATION_ALLOWED_SOURCES: frozenset[SourceType] = frozenset(
    LICENCE_REGISTRATION_PRECEDENCE_V1.keys()
)


class ClaimsDomainError(ValueError):
    """Base class for structural violations of the claims domain contract."""


class InvalidClassificationClaimError(ClaimsDomainError):
    """Raised when a ``ClassificationClaim``'s own fields are structurally invalid."""


class InvalidClaimEventError(ClaimsDomainError):
    """Raised when a single ``ClaimEvent`` violates a structural invariant that
    can be checked from its own fields alone (no other records needed)."""


class InvalidRuleSetVersionError(ClaimsDomainError):
    """Raised when a ``RuleSetVersion``'s own fields are structurally invalid."""


def is_timezone_aware(value: Any) -> bool:
    """True iff value is a ``datetime`` with an actual (non-None) UTC offset —
    the standard way to distinguish a real timezone-aware datetime from a
    naive one or a non-datetime value."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _require_non_empty_str(
    value: Any, *, field: str, error: type[ClaimsDomainError]
) -> None:
    if not isinstance(value, str) or not value:
        raise error(f"{field} must be a non-empty string, got {value!r}")


def _require_tz_aware(
    value: Any, *, field: str, error: type[ClaimsDomainError]
) -> None:
    if not is_timezone_aware(value):
        raise error(f"{field} must be a timezone-aware datetime, got {value!r}")


def _deep_freeze(obj: Any) -> Any:
    """Recursively convert mappings to ``MappingProxyType`` and lists/tuples to
    ``tuple``, always building new containers — never wrapping the caller's
    original mutable object, so a later mutation of the caller's dict/list
    cannot reach the frozen copy. Accepts an already-frozen Mapping too (a
    caller passing one of the module-level precedence constants directly)."""
    if isinstance(obj, Mapping):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


@dataclass(frozen=True)
class ClassificationClaim:
    claim_id: str
    company_id: int
    claim_type: ClaimType
    predicate: str
    value_json: dict
    source_type: SourceType
    source_reliability: float
    extraction_confidence: float
    extraction_method: str
    rule_set_version_id: str
    primary_evidence_content_hash: str
    observed_at: datetime
    effective_at: datetime
    extracted_at: datetime
    idempotency_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.company_id, bool)
            or not isinstance(self.company_id, int)
            or self.company_id <= 0
        ):
            raise InvalidClassificationClaimError(
                f"company_id must be a positive integer, got {self.company_id!r}"
            )

        for field_name, value in (
            ("claim_id", self.claim_id),
            ("predicate", self.predicate),
            ("extraction_method", self.extraction_method),
            ("rule_set_version_id", self.rule_set_version_id),
        ):
            _require_non_empty_str(
                value, field=field_name, error=InvalidClassificationClaimError
            )

        for field_name, value in (
            ("source_reliability", self.source_reliability),
            ("extraction_confidence", self.extraction_confidence),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not (0.0 <= value <= 1.0)
            ):
                raise InvalidClassificationClaimError(
                    f"{field_name} must be a finite number in [0, 1], got {value!r}"
                )

        for field_name, value in (
            ("primary_evidence_content_hash", self.primary_evidence_content_hash),
            ("idempotency_key", self.idempotency_key),
        ):
            if not is_valid_sha256(value):
                raise InvalidClassificationClaimError(
                    f"{field_name} must be a lowercase SHA-256 hex digest, got {value!r}"
                )

        for field_name, value in (
            ("observed_at", self.observed_at),
            ("effective_at", self.effective_at),
            ("extracted_at", self.extracted_at),
            ("created_at", self.created_at),
        ):
            _require_tz_aware(
                value, field=field_name, error=InvalidClassificationClaimError
            )

        object.__setattr__(self, "value_json", _deep_freeze(self.value_json))


@dataclass(frozen=True)
class ClaimEvent:
    event_id: str
    claim_id: str
    event_type: EventType
    related_claim_id: str | None
    actor_type: ActorType
    actor_id: str
    rationale: str | None
    rule_set_version_id: str
    event_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("event_id", self.event_id),
            ("claim_id", self.claim_id),
            ("actor_id", self.actor_id),
            ("rule_set_version_id", self.rule_set_version_id),
        ):
            _require_non_empty_str(
                value, field=field_name, error=InvalidClaimEventError
            )

        for field_name, value in (
            ("event_at", self.event_at),
            ("created_at", self.created_at),
        ):
            _require_tz_aware(value, field=field_name, error=InvalidClaimEventError)

        if (
            self.event_type == EventType.ADJUDICATED
            and self.actor_type != ActorType.HUMAN
        ):
            raise InvalidClaimEventError(
                "adjudicated events must be human-authored (actor_type=HUMAN); "
                f"got actor_type={self.actor_type!r}"
            )
        if self.event_type == EventType.SUPERSEDED:
            if self.related_claim_id is None:
                raise InvalidClaimEventError(
                    "superseded events require related_claim_id"
                )
            if self.related_claim_id == self.claim_id:
                raise InvalidClaimEventError("related_claim_id must not equal claim_id")
        elif self.related_claim_id is not None:
            raise InvalidClaimEventError(
                "related_claim_id is only permitted for superseded events, "
                f"not {self.event_type.value}"
            )


@dataclass(frozen=True)
class RuleSetVersion:
    rule_set_version_id: str
    claim_type: ClaimType
    precedence: Mapping[SourceType, int]
    staleness_threshold: timedelta
    effective_from: datetime

    def __post_init__(self) -> None:
        _require_non_empty_str(
            self.rule_set_version_id,
            field="rule_set_version_id",
            error=InvalidRuleSetVersionError,
        )
        _require_tz_aware(
            self.effective_from,
            field="effective_from",
            error=InvalidRuleSetVersionError,
        )

        if not isinstance(
            self.staleness_threshold, timedelta
        ) or self.staleness_threshold < timedelta(0):
            raise InvalidRuleSetVersionError(
                f"staleness_threshold must be a non-negative timedelta, got {self.staleness_threshold!r}"
            )

        if not self.precedence:
            raise InvalidRuleSetVersionError("precedence must not be empty")
        for source_type, tier in self.precedence.items():
            if not isinstance(source_type, SourceType):
                raise InvalidRuleSetVersionError(
                    f"precedence key is not a SourceType: {source_type!r}"
                )
            if isinstance(tier, bool) or not isinstance(tier, int) or tier < 1:
                raise InvalidRuleSetVersionError(
                    f"precedence tier for {source_type!r} must be a positive integer, got {tier!r}"
                )

        if self.claim_type == ClaimType.LICENCE_REGISTRATION:
            disallowed = set(self.precedence) - LICENCE_REGISTRATION_ALLOWED_SOURCES
            if disallowed:
                raise InvalidRuleSetVersionError(
                    "licence_registration rule sets may only use "
                    f"{sorted(s.value for s in LICENCE_REGISTRATION_ALLOWED_SOURCES)}, "
                    f"got disallowed sources: {sorted(s.value for s in disallowed)}"
                )
        elif self.claim_type == ClaimType.SECTOR_CLASSIFICATION:
            disallowed = set(self.precedence) - set(SourceType)
            if disallowed:
                raise InvalidRuleSetVersionError(
                    "sector_classification rule sets may only use the seven approved "
                    f"source types, got disallowed sources: {sorted(s.value for s in disallowed)}"
                )

        object.__setattr__(self, "precedence", _deep_freeze(self.precedence))


@dataclass(frozen=True)
class ResolvedBelief:
    winning_claim_id: str
    resolution_status: ResolutionStatus
    resolution_confidence: float
    competing_claim_count: int
    effective_as_of: datetime
    knowledge_as_of: datetime


@dataclass(frozen=True)
class NoBelief:
    """Sentinel: no applicable, non-terminal claim exists for the requested key
    within the (effective_as_of, knowledge_as_of) bitemporal window."""
