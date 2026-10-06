"""Result/outcome types for the external-action connector contract
(AI-Workforce PR1). Pure data + classification logic -- no network call,
no DB dependency, no reference to actions/action_approvals (which do not
exist yet).

Two separate outcome vocabularies, deliberately not merged into one enum:

  * ExecutionOutcome -- the result of ONE attempt to create/update an
    external object.
  * ReconciliationOutcome -- the result of asking "did that attempt's
    object actually get created?" after an AMBIGUOUS ExecutionResult.

Architecture-review corrections encoded here:
  - access_denied is its own ExecutionOutcome, not folded into FAILED
    (different remediation: fix the connection, not the payload).
  - A 4xx is never universally treated as a final FAILED -- classification
    is per (provider, operation) via ProviderOperationPolicy, and an
    unregistered pair gets the safest possible default, not a guess.
  - ReconciliationOutcome has four states, not two -- an empty search
    result is INCONCLUSIVE, never automatically CONFIRMED_ABSENT.
    CONFIRMED_ABSENT may only be returned with an AbsenceProof attached,
    and that proof must positively demonstrate full, scoped, error-free
    coverage -- see AbsenceProof below. This registry starts with zero
    providers registered, because the pilot's real target system is
    still unconfirmed.
  - Whether an HTTP response is "transient" (doesn't by itself tell you
    what happened -- e.g. a 429 or a 5xx) is a SEPARATE question from
    whether retrying the external ACTION is safe. classify_http_status()
    only ever answers the first question and returns AMBIGUOUS for it;
    nothing in this module grants permission to call create_object()
    again. That permission can only ever come from a ReconciliationResult
    with outcome=CONFIRMED_ABSENT and a sufficient AbsenceProof -- see
    pipeline/integrations/connector.py's docstring for the full flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExecutionOutcome(str, Enum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    ACCESS_DENIED = "access_denied"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of one create_object()/update_object() attempt.

    external_object_id is required when outcome is CONFIRMED (that IS the
    confirmation -- per the architecture review, nothing else counts) and
    forbidden otherwise, so a caller can never accidentally treat a
    non-confirmed result as if it named a real object.

    `detail` must stay a short, safe-to-log summary -- never a raw
    exception message, stack trace, or full request/response dump (which
    could contain the client's own external credentials or data).
    """

    outcome: ExecutionOutcome
    external_object_id: str | None = None
    http_status: int | None = None
    provider_error_code: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is ExecutionOutcome.CONFIRMED and not self.external_object_id:
            raise ValueError("CONFIRMED outcome requires external_object_id")
        if (
            self.outcome is not ExecutionOutcome.CONFIRMED
            and self.external_object_id is not None
        ):
            raise ValueError(
                f"external_object_id must be unset for outcome={self.outcome.value}"
            )


class ReconciliationOutcome(str, Enum):
    CONFIRMED_PRESENT = "confirmed_present"
    CONFIRMED_ABSENT = "confirmed_absent"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


@dataclass(frozen=True)
class AbsenceProof:
    """Evidence a connector must hold BEFORE it may return
    ReconciliationOutcome.CONFIRMED_ABSENT. Every field must be positively
    true -- a missing or merely-unset field is never treated as
    sufficient, and an empty search result, a reconciliation-call error,
    or an under-scoped/under-privileged credential must never be allowed
    to collapse into this.

    Fields, each answering one way a search can falsely come back empty:
      - read_consistency: must be "strong" (the target API's read for
        this operation is documented as immediately consistent with its
        own writes -- never "eventual", which could show an empty result
        for an object that was, in fact, just created).
      - search_fully_paginated: the search traversed every page/cursor of
        the result set -- a first-page-only check that happens to come
        back empty is not proof of anything beyond the first page.
      - sufficient_scope_confirmed: the credential/connection used for
        this search was independently confirmed to have the scope/
        permission needed to SEE the object if it existed -- a 200 OK
        with zero results from an under-scoped token looks identical to
        genuine absence and must not be conflated with it.
      - no_error_encountered: the search call itself completed without
        any error (network, auth, rate-limit, or otherwise) -- a
        reconciliation call that itself failed proves nothing about the
        object's existence.
      - provider: str, operation: str -- which policy authorized this
        proof, so a proof object can never silently get reused across an
        unrelated provider/operation pair it was never validated for.
    """

    provider: str
    operation: str
    read_consistency: str
    search_fully_paginated: bool
    sufficient_scope_confirmed: bool
    no_error_encountered: bool

    def is_sufficient(self) -> bool:
        return (
            self.read_consistency == "strong"
            and self.search_fully_paginated
            and self.sufficient_scope_confirmed
            and self.no_error_encountered
        )


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of a find_by_reference() call.

    CONFIRMED_ABSENT requires an `absence_proof` whose
    `is_sufficient()` is True -- constructing a CONFIRMED_ABSENT result
    without one, or with an insufficient one, raises. This is the
    structural enforcement of "an error, an empty page, or insufficient
    scope must never become proof of absence" -- it is not left to each
    connector implementation's discipline.

    30 minutes (or any other lease/timeout duration) of silence is
    NEVER, by itself, an AbsenceProof field -- there is deliberately no
    "elapsed_without_response" field here; time passing is not evidence.
    """

    outcome: ReconciliationOutcome
    external_object_id: str | None = None
    absence_proof: AbsenceProof | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if (
            self.outcome is ReconciliationOutcome.CONFIRMED_PRESENT
            and not self.external_object_id
        ):
            raise ValueError("CONFIRMED_PRESENT requires external_object_id")
        if (
            self.outcome is not ReconciliationOutcome.CONFIRMED_PRESENT
            and self.external_object_id is not None
        ):
            raise ValueError(
                f"external_object_id must be unset for outcome={self.outcome.value}"
            )
        if self.outcome is ReconciliationOutcome.CONFIRMED_ABSENT:
            if self.absence_proof is None:
                raise ValueError("CONFIRMED_ABSENT requires an absence_proof")
            if not self.absence_proof.is_sufficient():
                raise ValueError(
                    "CONFIRMED_ABSENT requires a SUFFICIENT absence_proof "
                    "(strong consistency, full pagination, confirmed scope, "
                    "no error) -- got an insufficient one"
                )
        elif self.absence_proof is not None:
            raise ValueError(
                f"absence_proof must be unset for outcome={self.outcome.value}"
            )


@dataclass(frozen=True)
class ProviderOperationPolicy:
    """Per-(provider, operation) classification policy.

    supports_absence_proof=False is the only safe default: it means a
    connector's find_by_reference() may never return CONFIRMED_ABSENT for
    this provider/operation at all (regardless of what evidence it thinks
    it has) -- which in turn means the (future) executor may never
    auto-retry a create, only escalate to manual review. This must be
    flipped to True only after confirming, against the real provider's
    real API docs, that a strongly-consistent get-by-reference lookup
    exists for this exact operation -- never assumed true by convenience.

    `transient_response_statuses` names statuses where the HTTP response
    ALONE doesn't tell you what happened server-side (classic example:
    429 -- the request may or may not have been processed before the
    limiter kicked in). This field answers ONLY "is the response itself
    inconclusive", classified as AMBIGUOUS -- it grants no permission to
    retry the underlying action. Retrying create_object() is only ever
    authorized by a sufficient AbsenceProof-backed CONFIRMED_ABSENT
    result, regardless of which status produced the AMBIGUOUS outcome.
    This field is intentionally NOT named "retryable_*" to avoid implying
    otherwise.
    """

    provider: str
    operation: str
    supports_absence_proof: bool = False
    transient_response_statuses: frozenset[int] = field(default_factory=frozenset)
    access_denied_http_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({401, 403})
    )

    def classify_http_status(self, status: int) -> ExecutionOutcome:
        """Pure classification from an HTTP status alone. Answers only
        'what does this response tell us' -- never 'is it safe to retry
        the action'. A 2xx result here only means 'treat as
        CONFIRMED-shaped' -- the caller still must supply a real
        external_object_id to actually construct an
        ExecutionResult(outcome=CONFIRMED, ...); this function cannot do
        that on its own, and nothing it returns ever authorizes a second
        create_object() call by itself.
        """
        if status in self.access_denied_http_statuses:
            return ExecutionOutcome.ACCESS_DENIED
        if status in self.transient_response_statuses:
            return ExecutionOutcome.AMBIGUOUS
        if 200 <= status < 300:
            return ExecutionOutcome.CONFIRMED
        if 400 <= status < 500:
            return ExecutionOutcome.FAILED
        # 5xx and anything unclassified: never assumed to be a clean,
        # final failure -- the request may have completed server-side
        # despite an error response on the way back.
        return ExecutionOutcome.AMBIGUOUS


class ProviderPolicyRegistry:
    """Starts empty. An unregistered (provider, operation) pair resolves
    to ProviderOperationPolicy's own safe defaults, not an error -- so
    every provider/operation this PR has never heard of is still handled,
    just conservatively (no absence proof, only 401/403 is access_denied,
    everything else ambiguous)."""

    def __init__(self) -> None:
        self._policies: dict[tuple[str, str], ProviderOperationPolicy] = {}

    def register(self, policy: ProviderOperationPolicy) -> None:
        self._policies[(policy.provider, policy.operation)] = policy

    def get(self, provider: str, operation: str) -> ProviderOperationPolicy:
        return self._policies.get(
            (provider, operation),
            ProviderOperationPolicy(provider=provider, operation=operation),
        )
