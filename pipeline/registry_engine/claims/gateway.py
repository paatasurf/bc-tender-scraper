"""Classification Claims Gateway (PR-B2) — the only sanctioned write path into
the migration-029 schema.

This module never resolves ``DATABASE_URL``/``DATABASE_URL_PRODUCTION`` and
never calls a ``db.db_safety`` guard itself: it only imports the
``ClaimsWriteCapability`` type and the package-internal ``_unwrap_engine``
helper from ``gateway_capability.py`` (never the guard-touching factory).
Every write function here requires a caller to already hold a capability,
obtained through ``gateway_capability.acquire_claims_write_capability()`` —
which is the one place in this package that runs the existing Class C/D
guard (a real human TTY confirmation for production; agents/CI cannot
satisfy it, see ``db/db_safety.py``). A raw ``Engine`` is never sufficient
for a write: ``submit_claim(..., dry_run=False)`` and ``record_event(...)``
both check ``isinstance(target, ClaimsWriteCapability)`` as the very first
thing they do and raise :class:`UnauthorizedWriteError` before issuing a
single SQL statement if it is not one. Read-only ``dry_run=True`` calls
accept either a plain ``Engine`` or a capability (its wrapped engine is
resolved internally via ``_unwrap_engine``, never through a public
attribute -- see ``gateway_capability.py``'s module docstring for what this
check is and is not a guarantee against: a fail-closed barrier against
accidentally bypassing the public API, not an unforgeable security
boundary).

Everything a claim needs is generated in Python, never by the database:
``claim_id``/``claim_evidence_id``/``event_id`` via ``uuid.uuid4()``,
``content_hash``/``idempotency_key`` via the PR-A canonical functions
(``compute_evidence_fingerprint`` / ``compute_claim_idempotency_key``). The
tables have no server-side ``DEFAULT`` for these columns (see migration 029)
by design.

``submit_claim`` is atomic and fail-closed: the claim row and its one
required (primary) evidence row are inserted inside a single transaction.
If the evidence insert fails for any reason, the claim insert is rolled back
with it — this schema and this Gateway never produce a claim with zero
evidence rows. Idempotency is enforced with
``ON CONFLICT (idempotency_key) DO NOTHING``; a repeat submission of an
already-recorded fact returns :class:`ClaimAlreadyExistsResult` rather than
raising or creating a duplicate. Every structural/business-rule validation
below runs unconditionally, before the ``dry_run`` branch point — dry-run and
apply always see exactly the same checks; dry-run can never approve a
payload that apply would then reject.

``record_event`` locks the *parent* ``classification_claims`` row with
``SELECT ... FOR UPDATE`` before checking for an existing terminal event on
``claim_events``. Locking a (possibly nonexistent) row in ``claim_events``
itself would not serialize anything -- there is nothing to lock until a row
already exists, so two concurrent writers could both pass a naive
"does an event already exist?" check before either commits. The parent claim
row, by contrast, is guaranteed to already exist, so locking it is a real
synchronization point: only one transaction can hold it at a time, making the
"check then insert" sequence on ``claim_events`` atomic per claim. Once
locked, the event's ``rule_set_version_id`` is checked against the *parent
claim's* claim_type and against ``event_at`` — same compatibility rule
``submit_claim`` applies to a fresh claim -- inside that same transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from pipeline.registry_engine.claims.canonical import (
    compute_claim_idempotency_key,
    compute_evidence_fingerprint,
)
from pipeline.registry_engine.claims.domain import (
    ActorType,
    ClaimEvent,
    ClaimType,
    ClassificationClaim,
    EventType,
    LICENCE_REGISTRATION_ALLOWED_SOURCES,
    SourceType,
)
from pipeline.registry_engine.claims.gateway_capability import (
    ClaimsWriteCapability,
    UnauthorizedWriteError,
    _unwrap_engine,
)
from pipeline.registry_engine.claims.resolver import IncompatibleRuleSetVersionError

__all__ = [
    "ClaimAlreadyExistsResult",
    "ClaimDryRunResult",
    "ClaimNotFoundError",
    "ClaimSubmitted",
    "ClaimsGatewayError",
    "ClaimsWriteCapability",
    "CompanyNotFoundError",
    "CrossScopeRelatedClaimError",
    "EVIDENCE_SOURCES",
    "EvidenceInsertFailedError",
    "IncompatibleRuleSetVersionError",
    "InvalidClaimTimestampsError",
    "InvalidClaimValueError",
    "InvalidEvidenceLocatorError",
    "InvalidEvidenceSourceError",
    "InvalidPredicateForClaimTypeError",
    "LICENCE_REGISTRATION_PREDICATES",
    "LICENCE_STATUS_VALUES",
    "LicenceSourceNotAllowedError",
    "RecordedEvent",
    "RelatedClaimNotFoundError",
    "RuleSetVersionNotFoundError",
    "SECTOR_CLASSIFICATION_PREDICATES",
    "SubmitClaimOutcome",
    "TerminalEventAlreadyExistsError",
    "UnauthorizedWriteError",
    "record_event",
    "submit_claim",
]

# Mirrors the DB CHECK constraint ck_claim_type_predicate /
# ck_classification_claims_claim_type in 029_classification_claims.sql
# exactly -- validated here so a bad predicate/claim_type pairing gets a
# typed error before any DB round trip, not a raw IntegrityError.
SECTOR_CLASSIFICATION_PREDICATES: frozenset[str] = frozenset(
    {"dominant_sector", "primary_trade"}
)
LICENCE_REGISTRATION_PREDICATES: frozenset[str] = frozenset(
    {"licence_identifier", "business_number"}
)

# Mirrors ck_claim_evidence_source exactly.
EVIDENCE_SOURCES: frozenset[str] = frozenset(
    {
        "kg_observation",
        "permit",
        "contract_award",
        "tender_outcome",
        "licence_authority_raw",
        "government_registry_raw",
        "external_url",
    }
)

# V1 value_json contract for licence_identifier claims. Not a DB CHECK (JSONB
# internal shape isn't checkable there) -- enforced only here, identically
# for dry-run and apply (see module docstring).
LICENCE_STATUS_VALUES: frozenset[str] = frozenset({"active", "expired", "suspended"})

_DOMINANT_SECTOR_KEYS: frozenset[str] = frozenset({"sector"})
_PRIMARY_TRADE_KEYS: frozenset[str] = frozenset({"trade"})
_BUSINESS_NUMBER_KEYS: frozenset[str] = frozenset({"business_number"})
_LICENCE_IDENTIFIER_KEYS: frozenset[str] = frozenset(
    {"licence_identifier", "issuing_authority", "status", "expiry_date"}
)


class ClaimsGatewayError(RuntimeError):
    """Base class for Gateway-level operational failures -- violations that
    depend on external/database state (missing company, missing or
    incompatible rule set, a conflicting event transition) or on a
    submission's business-rule shape (evidence vocabulary, value_json
    contract), as opposed to
    :class:`~pipeline.registry_engine.claims.domain.ClaimsDomainError`, which
    covers purely structural violations of a single record's own fields."""


class CompanyNotFoundError(ClaimsGatewayError):
    """Raised when ``company_id`` does not exist in ``companies``."""


class RuleSetVersionNotFoundError(ClaimsGatewayError):
    """Raised when ``rule_set_version_id`` does not exist in ``rule_set_versions``."""


class LicenceSourceNotAllowedError(ClaimsGatewayError):
    """Raised when a ``licence_registration`` claim's ``source_type`` is not
    in :data:`LICENCE_REGISTRATION_ALLOWED_SOURCES`."""


class InvalidPredicateForClaimTypeError(ClaimsGatewayError):
    """Raised when ``predicate`` is not one of the predicates permitted for
    the given ``claim_type`` (see :data:`SECTOR_CLASSIFICATION_PREDICATES` /
    :data:`LICENCE_REGISTRATION_PREDICATES`)."""


class InvalidEvidenceSourceError(ClaimsGatewayError):
    """Raised when ``evidence_source`` is not in :data:`EVIDENCE_SOURCES`."""


class InvalidEvidenceLocatorError(ClaimsGatewayError):
    """Raised when ``evidence_locator`` is not a non-empty JSON object."""


class InvalidClaimTimestampsError(ClaimsGatewayError):
    """Raised when ``effective_at`` is before ``observed_at``."""


class InvalidClaimValueError(ClaimsGatewayError):
    """Raised when ``value_json`` does not match the exact V1 shape required
    for its predicate (see module docstring / :data:`LICENCE_STATUS_VALUES`).
    Extra keys are rejected (fail closed) -- V1 has no forward-compatible
    schema-evolution story for ``value_json`` yet."""


class EvidenceInsertFailedError(ClaimsGatewayError):
    """Raised when the primary evidence INSERT fails after the claim INSERT
    succeeded within the same transaction. Raising here (inside the
    ``with engine.begin()`` block) triggers an automatic ROLLBACK of the
    claim insert too -- this Gateway never leaves a claim without evidence."""


class ClaimNotFoundError(ClaimsGatewayError):
    """Raised by ``record_event`` when ``claim_id`` does not exist."""


class RelatedClaimNotFoundError(ClaimsGatewayError):
    """Raised by ``record_event`` when a ``superseded`` event's
    ``related_claim_id`` does not exist."""


class CrossScopeRelatedClaimError(ClaimsGatewayError):
    """Raised by ``record_event`` when a ``superseded`` event's
    ``related_claim_id`` does not share (company_id, claim_type, predicate)
    with the claim it is attached to."""


class TerminalEventAlreadyExistsError(ClaimsGatewayError):
    """Raised by ``record_event`` when the target claim already has a
    terminal event -- a typed transition error, not a raw IntegrityError
    from the ``uq_claim_events_one_per_claim`` unique index."""


@dataclass(frozen=True)
class ClaimDryRunResult:
    """Outcome of ``submit_claim(..., dry_run=True)`` -- validation, hashing,
    and read-only analysis only. No row is written."""

    would_create: bool
    idempotency_key: str
    content_hash: str
    existing_claim_id: str | None


@dataclass(frozen=True)
class ClaimSubmitted:
    """A genuinely new claim + primary evidence row were inserted and committed."""

    claim_id: str
    claim_evidence_id: str
    idempotency_key: str
    content_hash: str


@dataclass(frozen=True)
class ClaimAlreadyExistsResult:
    """The idempotency key already existed -- nothing was inserted, no
    duplicate was created. ``claim_id`` is the pre-existing row's id."""

    claim_id: str
    idempotency_key: str


SubmitClaimOutcome = ClaimDryRunResult | ClaimSubmitted | ClaimAlreadyExistsResult


@dataclass(frozen=True)
class RecordedEvent:
    """A terminal ``claim_events`` row was inserted and committed."""

    event_id: str
    claim_id: str
    event_type: EventType


def _validate_predicate_for_claim_type(claim_type: ClaimType, predicate: str) -> None:
    allowed = (
        SECTOR_CLASSIFICATION_PREDICATES
        if claim_type == ClaimType.SECTOR_CLASSIFICATION
        else LICENCE_REGISTRATION_PREDICATES
    )
    if predicate not in allowed:
        raise InvalidPredicateForClaimTypeError(
            f"predicate {predicate!r} is not permitted for claim_type "
            f"{claim_type.value!r}; allowed: {sorted(allowed)}"
        )


def _validate_evidence_source(evidence_source: str) -> None:
    if evidence_source not in EVIDENCE_SOURCES:
        raise InvalidEvidenceSourceError(
            f"evidence_source {evidence_source!r} is not in the approved "
            f"vocabulary: {sorted(EVIDENCE_SOURCES)}"
        )


def _validate_evidence_locator(evidence_locator: dict) -> None:
    if not isinstance(evidence_locator, dict) or not evidence_locator:
        raise InvalidEvidenceLocatorError(
            f"evidence_locator must be a non-empty JSON object, got {evidence_locator!r}"
        )


def _validate_claim_timestamps(
    *, observed_at: datetime, effective_at: datetime
) -> None:
    if effective_at < observed_at:
        raise InvalidClaimTimestampsError(
            f"effective_at ({effective_at!r}) must not be before "
            f"observed_at ({observed_at!r})"
        )


def _require_exact_keys(
    value_json: dict, required_keys: frozenset[str], *, predicate: str
) -> None:
    actual_keys = set(value_json.keys())
    if actual_keys != required_keys:
        raise InvalidClaimValueError(
            f"value_json for predicate {predicate!r} must have exactly the "
            f"keys {sorted(required_keys)}, got {sorted(actual_keys)}"
        )


def _require_non_empty_string_field(
    value_json: dict, key: str, *, predicate: str
) -> None:
    value = value_json.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidClaimValueError(
            f"value_json.{key} for predicate {predicate!r} must be a "
            f"non-empty string, got {value!r}"
        )


def _validate_value_json(*, predicate: str, value_json: dict) -> None:
    """Exact V1 shape per predicate. Extra keys are rejected (fail closed) --
    see module/class docstrings for the policy rationale. The very same
    function backs both dry_run=True and dry_run=False; there is no separate
    apply-only check."""
    if not isinstance(value_json, dict):
        raise InvalidClaimValueError(
            f"value_json must be a JSON object, got {type(value_json).__name__}"
        )

    if predicate == "dominant_sector":
        _require_exact_keys(value_json, _DOMINANT_SECTOR_KEYS, predicate=predicate)
        _require_non_empty_string_field(value_json, "sector", predicate=predicate)
    elif predicate == "primary_trade":
        _require_exact_keys(value_json, _PRIMARY_TRADE_KEYS, predicate=predicate)
        _require_non_empty_string_field(value_json, "trade", predicate=predicate)
    elif predicate == "business_number":
        _require_exact_keys(value_json, _BUSINESS_NUMBER_KEYS, predicate=predicate)
        _require_non_empty_string_field(
            value_json, "business_number", predicate=predicate
        )
    elif predicate == "licence_identifier":
        _require_exact_keys(value_json, _LICENCE_IDENTIFIER_KEYS, predicate=predicate)
        _require_non_empty_string_field(
            value_json, "licence_identifier", predicate=predicate
        )
        _require_non_empty_string_field(
            value_json, "issuing_authority", predicate=predicate
        )
        status = value_json.get("status")
        if status not in LICENCE_STATUS_VALUES:
            raise InvalidClaimValueError(
                f"value_json.status must be one of {sorted(LICENCE_STATUS_VALUES)}, "
                f"got {status!r}"
            )
        expiry_date = value_json.get("expiry_date")
        if expiry_date is not None:
            if not isinstance(expiry_date, str):
                raise InvalidClaimValueError(
                    "value_json.expiry_date must be an ISO date string or "
                    f"null, got {expiry_date!r}"
                )
            try:
                date.fromisoformat(expiry_date)
            except ValueError as exc:
                raise InvalidClaimValueError(
                    f"value_json.expiry_date is not a valid ISO date: {expiry_date!r}"
                ) from exc
    else:  # pragma: no cover - unreachable, predicate already validated above
        raise InvalidPredicateForClaimTypeError(f"unknown predicate: {predicate!r}")


def _check_company_exists(conn: Connection, company_id: int) -> None:
    exists = (
        conn.execute(
            text("SELECT 1 FROM companies WHERE id = :company_id"),
            {"company_id": company_id},
        ).first()
        is not None
    )
    if not exists:
        raise CompanyNotFoundError(f"company_id {company_id} does not exist")


def _check_rule_set_compatibility(
    conn: Connection,
    *,
    rule_set_version_id: str,
    claim_type_value: str,
    at_time: datetime,
) -> None:
    """Shared by submit_claim (against the new claim's effective_at) and
    record_event (against the parent claim's claim_type and the event's
    event_at) -- one compatibility rule, one place it is checked."""
    row = conn.execute(
        text(
            "SELECT claim_type, effective_from FROM rule_set_versions "
            "WHERE rule_set_version_id = :rule_set_version_id"
        ),
        {"rule_set_version_id": rule_set_version_id},
    ).first()
    if row is None:
        raise RuleSetVersionNotFoundError(
            f"rule_set_version_id {rule_set_version_id!r} does not exist"
        )
    rs_claim_type, rs_effective_from = row
    if rs_claim_type != claim_type_value:
        raise IncompatibleRuleSetVersionError(
            f"rule_set_version {rule_set_version_id!r} has claim_type={rs_claim_type!r}, "
            f"does not match claim_type={claim_type_value!r}"
        )
    if rs_effective_from > at_time:
        raise IncompatibleRuleSetVersionError(
            f"rule_set_version {rule_set_version_id!r} is not yet effective "
            f"(effective_from={rs_effective_from!r} > {at_time!r})"
        )


def _resolve_readonly_engine(target: Engine | ClaimsWriteCapability) -> Engine:
    if isinstance(target, ClaimsWriteCapability):
        return _unwrap_engine(target)
    return target


def _require_write_capability(target: object) -> ClaimsWriteCapability:
    if not isinstance(target, ClaimsWriteCapability):
        raise UnauthorizedWriteError(
            "a write to the classification-claims schema was attempted without "
            "a ClaimsWriteCapability. Acquire one via "
            "gateway_capability.acquire_claims_write_capability(script_name, "
            "allow_production=...) -- which runs the existing Class C/D "
            "production guard -- and pass it here instead of a raw Engine. "
            f"Got: {type(target).__name__}"
        )
    return target


def submit_claim(
    target: Engine | ClaimsWriteCapability,
    *,
    company_id: int,
    claim_type: ClaimType,
    predicate: str,
    value_json: dict,
    source_type: SourceType,
    source_reliability: float,
    extraction_confidence: float,
    extraction_method: str,
    rule_set_version_id: str,
    evidence_source: str,
    evidence_locator: dict,
    observed_at: datetime,
    effective_at: datetime,
    payload_digest: str | None = None,
    dry_run: bool = True,
) -> SubmitClaimOutcome:
    """Validate and (if ``dry_run=False``) atomically insert one
    classification claim plus its one required primary evidence row.

    ``target`` may be a plain ``Engine`` for ``dry_run=True`` (the default).
    ``dry_run=False`` requires a :class:`ClaimsWriteCapability` -- passing a
    raw ``Engine`` raises :class:`UnauthorizedWriteError` immediately, before
    any validation or SQL runs.

    ``dry_run=True``: runs every validation and read-only check below,
    computes the content hash and idempotency key, and reports whether a
    matching claim already exists -- never writes.

    ``dry_run=False``: re-runs the exact same checks inside the write
    transaction and then, atomically: INSERT classification_claims (ON
    CONFLICT (idempotency_key) DO NOTHING) -> INSERT claim_evidence ->
    COMMIT. Any failure rolls back both inserts.
    """
    if not dry_run:
        _require_write_capability(target)

    _validate_predicate_for_claim_type(claim_type, predicate)
    if (
        claim_type == ClaimType.LICENCE_REGISTRATION
        and source_type not in LICENCE_REGISTRATION_ALLOWED_SOURCES
    ):
        raise LicenceSourceNotAllowedError(
            f"source_type {source_type.value!r} is not permitted for "
            "licence_registration claims; allowed: "
            f"{sorted(s.value for s in LICENCE_REGISTRATION_ALLOWED_SOURCES)}"
        )
    _validate_evidence_source(evidence_source)
    _validate_evidence_locator(evidence_locator)
    _validate_claim_timestamps(observed_at=observed_at, effective_at=effective_at)
    _validate_value_json(predicate=predicate, value_json=value_json)

    content_hash = compute_evidence_fingerprint(
        evidence_source=evidence_source,
        evidence_locator=evidence_locator,
        payload_digest=payload_digest,
    )
    idempotency_key = compute_claim_idempotency_key(
        company_id=company_id,
        claim_type=claim_type.value,
        predicate=predicate,
        source_type=source_type.value,
        primary_evidence_content_hash=content_hash,
        extraction_method=extraction_method,
        rule_set_version_id=rule_set_version_id,
        value_json=value_json,
    )

    now = datetime.now(timezone.utc)
    claim_id = str(uuid4())

    # Structural validation reused from PR-A: raises InvalidClassificationClaimError
    # on any violation (bad company_id, empty strings, out-of-range
    # confidence/reliability, malformed hashes, naive timestamps).
    ClassificationClaim(
        claim_id=claim_id,
        company_id=company_id,
        claim_type=claim_type,
        predicate=predicate,
        value_json=value_json,
        source_type=source_type,
        source_reliability=source_reliability,
        extraction_confidence=extraction_confidence,
        extraction_method=extraction_method,
        rule_set_version_id=rule_set_version_id,
        primary_evidence_content_hash=content_hash,
        observed_at=observed_at,
        effective_at=effective_at,
        extracted_at=now,
        idempotency_key=idempotency_key,
        created_at=now,
    )

    if dry_run:
        engine = _resolve_readonly_engine(target)
        with engine.connect() as conn:
            _check_company_exists(conn, company_id)
            _check_rule_set_compatibility(
                conn,
                rule_set_version_id=rule_set_version_id,
                claim_type_value=claim_type.value,
                at_time=effective_at,
            )
            existing = conn.execute(
                text(
                    "SELECT claim_id FROM classification_claims WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            ).first()
        return ClaimDryRunResult(
            would_create=existing is None,
            idempotency_key=idempotency_key,
            content_hash=content_hash,
            existing_claim_id=str(existing[0]) if existing is not None else None,
        )

    capability = _require_write_capability(target)
    with _unwrap_engine(capability).begin() as conn:
        _check_company_exists(conn, company_id)
        _check_rule_set_compatibility(
            conn,
            rule_set_version_id=rule_set_version_id,
            claim_type_value=claim_type.value,
            at_time=effective_at,
        )

        inserted = conn.execute(
            text("""
                INSERT INTO classification_claims (
                    claim_id, company_id, claim_type, predicate, value_json,
                    source_type, source_reliability, extraction_confidence,
                    extraction_method, rule_set_version_id,
                    primary_evidence_content_hash, observed_at, effective_at,
                    extracted_at, idempotency_key, created_at
                ) VALUES (
                    :claim_id, :company_id, :claim_type, :predicate, CAST(:value_json AS jsonb),
                    :source_type, :source_reliability, :extraction_confidence,
                    :extraction_method, :rule_set_version_id,
                    :primary_evidence_content_hash, :observed_at, :effective_at,
                    :extracted_at, :idempotency_key, :created_at
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING claim_id
                """),
            {
                "claim_id": claim_id,
                "company_id": company_id,
                "claim_type": claim_type.value,
                "predicate": predicate,
                "value_json": json.dumps(value_json),
                "source_type": source_type.value,
                "source_reliability": source_reliability,
                "extraction_confidence": extraction_confidence,
                "extraction_method": extraction_method,
                "rule_set_version_id": rule_set_version_id,
                "primary_evidence_content_hash": content_hash,
                "observed_at": observed_at,
                "effective_at": effective_at,
                "extracted_at": now,
                "idempotency_key": idempotency_key,
                "created_at": now,
            },
        ).first()

        if inserted is None:
            existing_row = conn.execute(
                text(
                    "SELECT claim_id FROM classification_claims WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            ).first()
            return ClaimAlreadyExistsResult(
                claim_id=str(existing_row[0]), idempotency_key=idempotency_key
            )

        actual_claim_id = str(inserted[0])
        claim_evidence_id = str(uuid4())
        try:
            conn.execute(
                text("""
                    INSERT INTO claim_evidence (
                        claim_evidence_id, claim_id, evidence_source,
                        evidence_locator, content_hash, created_at
                    ) VALUES (
                        :claim_evidence_id, :claim_id, :evidence_source,
                        CAST(:evidence_locator AS jsonb), :content_hash, :created_at
                    )
                    """),
                {
                    "claim_evidence_id": claim_evidence_id,
                    "claim_id": actual_claim_id,
                    "evidence_source": evidence_source,
                    "evidence_locator": json.dumps(evidence_locator),
                    "content_hash": content_hash,
                    "created_at": now,
                },
            )
        except IntegrityError as exc:
            raise EvidenceInsertFailedError(
                f"primary evidence insert failed for claim {actual_claim_id!r}; "
                "the claim insert is rolled back with it"
            ) from exc

        return ClaimSubmitted(
            claim_id=actual_claim_id,
            claim_evidence_id=claim_evidence_id,
            idempotency_key=idempotency_key,
            content_hash=content_hash,
        )


def record_event(
    target: ClaimsWriteCapability,
    *,
    claim_id: str,
    event_type: EventType,
    related_claim_id: str | None,
    actor_type: ActorType,
    actor_id: str,
    rule_set_version_id: str,
    event_at: datetime,
    rationale: str | None = None,
) -> RecordedEvent:
    """Atomically record one terminal event (``superseded`` / ``rejected`` /
    ``adjudicated``) against an existing claim. The whole operation is one
    transaction, owned entirely by this function.

    Always requires a :class:`ClaimsWriteCapability` -- there is no dry-run
    mode here. A raw ``Engine`` raises :class:`UnauthorizedWriteError`
    immediately, before any validation or SQL runs.
    """
    capability = _require_write_capability(target)
    event_id = str(uuid4())
    now = datetime.now(timezone.utc)

    # Structural validation reused from PR-A: raises InvalidClaimEventError
    # for adjudicated-by-non-human, missing/self-referential/misplaced
    # related_claim_id, empty actor_id, naive timestamps.
    ClaimEvent(
        event_id=event_id,
        claim_id=claim_id,
        event_type=event_type,
        related_claim_id=related_claim_id,
        actor_type=actor_type,
        actor_id=actor_id,
        rationale=rationale,
        rule_set_version_id=rule_set_version_id,
        event_at=event_at,
        created_at=now,
    )

    with _unwrap_engine(capability).begin() as conn:
        # Lock the parent claim FIRST -- see module docstring for why this
        # (and not locking claim_events) is what actually serializes the
        # race between two concurrent terminal-event submissions.
        parent = conn.execute(
            text(
                "SELECT company_id, claim_type, predicate FROM classification_claims "
                "WHERE claim_id = :claim_id FOR UPDATE"
            ),
            {"claim_id": claim_id},
        ).first()
        if parent is None:
            raise ClaimNotFoundError(f"claim_id {claim_id!r} does not exist")
        parent_company_id, parent_claim_type, parent_predicate = parent

        # Rule-set compatibility against the LOCKED parent's own claim_type,
        # not merely "does the rule set exist" -- same rule submit_claim
        # applies to a fresh claim, checked here in the same transaction.
        _check_rule_set_compatibility(
            conn,
            rule_set_version_id=rule_set_version_id,
            claim_type_value=parent_claim_type,
            at_time=event_at,
        )

        if event_type == EventType.SUPERSEDED:
            related = conn.execute(
                text(
                    "SELECT company_id, claim_type, predicate FROM classification_claims "
                    "WHERE claim_id = :claim_id"
                ),
                {"claim_id": related_claim_id},
            ).first()
            if related is None:
                raise RelatedClaimNotFoundError(
                    f"related_claim_id {related_claim_id!r} does not exist"
                )
            if tuple(related) != (
                parent_company_id,
                parent_claim_type,
                parent_predicate,
            ):
                raise CrossScopeRelatedClaimError(
                    f"related_claim_id {related_claim_id!r} is outside the "
                    "company_id/claim_type/predicate scope of the superseded claim"
                )

        # Only safe to check now that the parent row is locked -- see module
        # docstring: locking claim_events itself would not serialize this.
        existing_event = conn.execute(
            text("SELECT 1 FROM claim_events WHERE claim_id = :claim_id LIMIT 1"),
            {"claim_id": claim_id},
        ).first()
        if existing_event is not None:
            raise TerminalEventAlreadyExistsError(
                f"claim {claim_id!r} already has a terminal event"
            )

        try:
            conn.execute(
                text("""
                    INSERT INTO claim_events (
                        event_id, claim_id, event_type, related_claim_id,
                        actor_type, actor_id, rationale, rule_set_version_id,
                        event_at, created_at
                    ) VALUES (
                        :event_id, :claim_id, :event_type, :related_claim_id,
                        :actor_type, :actor_id, :rationale, :rule_set_version_id,
                        :event_at, :created_at
                    )
                    """),
                {
                    "event_id": event_id,
                    "claim_id": claim_id,
                    "event_type": event_type.value,
                    "related_claim_id": related_claim_id,
                    "actor_type": actor_type.value,
                    "actor_id": actor_id,
                    "rationale": rationale,
                    "rule_set_version_id": rule_set_version_id,
                    "event_at": event_at,
                    "created_at": now,
                },
            )
        except IntegrityError as exc:
            raise TerminalEventAlreadyExistsError(
                f"claim {claim_id!r} already has a terminal event"
            ) from exc

    return RecordedEvent(event_id=event_id, claim_id=claim_id, event_type=event_type)
