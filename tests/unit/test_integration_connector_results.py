"""Pure unit tests for pipeline/integrations/results.py -- no DB, no
network. Covers the ExecutionResult/ReconciliationResult validation
invariants and ProviderOperationPolicy's classification rules from the
architecture review corrections.
"""

from __future__ import annotations

import pytest

from pipeline.integrations.results import (
    AbsenceProof,
    ExecutionOutcome,
    ExecutionResult,
    ProviderOperationPolicy,
    ProviderPolicyRegistry,
    ReconciliationOutcome,
    ReconciliationResult,
)

_SUFFICIENT_PROOF = AbsenceProof(
    provider="jira",
    operation="create_issue",
    read_consistency="strong",
    search_fully_paginated=True,
    sufficient_scope_confirmed=True,
    no_error_encountered=True,
)


# ---------------------------------------------------------------------------
# ExecutionResult invariants
# ---------------------------------------------------------------------------


def test_confirmed_without_object_id_is_rejected():
    with pytest.raises(ValueError):
        ExecutionResult(outcome=ExecutionOutcome.CONFIRMED)


def test_confirmed_with_object_id_is_valid():
    result = ExecutionResult(outcome=ExecutionOutcome.CONFIRMED, external_object_id="TASK-1")
    assert result.external_object_id == "TASK-1"


@pytest.mark.parametrize(
    "outcome",
    [ExecutionOutcome.FAILED, ExecutionOutcome.ACCESS_DENIED, ExecutionOutcome.AMBIGUOUS],
)
def test_non_confirmed_outcome_with_object_id_is_rejected(outcome):
    with pytest.raises(ValueError):
        ExecutionResult(outcome=outcome, external_object_id="TASK-1")


def test_ambiguous_result_never_names_an_object():
    result = ExecutionResult(outcome=ExecutionOutcome.AMBIGUOUS, http_status=504)
    assert result.external_object_id is None


# ---------------------------------------------------------------------------
# ReconciliationResult invariants
# ---------------------------------------------------------------------------


def test_confirmed_present_without_object_id_is_rejected():
    with pytest.raises(ValueError):
        ReconciliationResult(outcome=ReconciliationOutcome.CONFIRMED_PRESENT)


@pytest.mark.parametrize(
    "outcome",
    [
        ReconciliationOutcome.INCONCLUSIVE,
        ReconciliationOutcome.ERROR,
    ],
)
def test_non_present_outcome_with_object_id_is_rejected(outcome):
    with pytest.raises(ValueError):
        ReconciliationResult(outcome=outcome, external_object_id="TASK-1")


def test_inconclusive_is_constructible_with_no_object_id():
    result = ReconciliationResult(outcome=ReconciliationOutcome.INCONCLUSIVE)
    assert result.external_object_id is None


# ---------------------------------------------------------------------------
# CONFIRMED_ABSENT / AbsenceProof -- architecture review correction 3
# ---------------------------------------------------------------------------


def test_confirmed_absent_without_any_proof_is_rejected():
    with pytest.raises(ValueError):
        ReconciliationResult(outcome=ReconciliationOutcome.CONFIRMED_ABSENT)


def test_confirmed_absent_with_sufficient_proof_is_valid():
    result = ReconciliationResult(
        outcome=ReconciliationOutcome.CONFIRMED_ABSENT, absence_proof=_SUFFICIENT_PROOF
    )
    assert result.outcome is ReconciliationOutcome.CONFIRMED_ABSENT


@pytest.mark.parametrize(
    "overrides",
    [
        {"read_consistency": "eventual"},
        {"search_fully_paginated": False},
        {"sufficient_scope_confirmed": False},
        {"no_error_encountered": False},
    ],
)
def test_confirmed_absent_with_any_single_missing_proof_field_is_rejected(overrides):
    """An empty page, an under-scoped credential, an error, or eventual
    (not strong) consistency must each INDIVIDUALLY be enough to block
    CONFIRMED_ABSENT -- none of these may be compensated for by the
    others being fine."""
    import dataclasses

    insufficient = dataclasses.replace(_SUFFICIENT_PROOF, **overrides)
    assert insufficient.is_sufficient() is False
    with pytest.raises(ValueError):
        ReconciliationResult(
            outcome=ReconciliationOutcome.CONFIRMED_ABSENT, absence_proof=insufficient
        )


def test_absence_proof_cannot_be_attached_to_other_outcomes():
    with pytest.raises(ValueError):
        ReconciliationResult(
            outcome=ReconciliationOutcome.INCONCLUSIVE, absence_proof=_SUFFICIENT_PROOF
        )


def test_no_field_exists_for_elapsed_time_as_proof():
    """There is deliberately no 'time elapsed without response' field on
    AbsenceProof -- a 30-minute (or any) lease timeout must never become
    evidence of absence just by existing as a duration."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(AbsenceProof)}
    assert not any("elapsed" in name or "timeout" in name or "lease" in name for name in field_names)


# ---------------------------------------------------------------------------
# ProviderOperationPolicy / ProviderPolicyRegistry
# ---------------------------------------------------------------------------


def test_unregistered_provider_operation_gets_the_safe_default():
    registry = ProviderPolicyRegistry()
    policy = registry.get("some_unknown_provider", "create_task")
    assert policy.supports_absence_proof is False
    assert policy.classify_http_status(401) is ExecutionOutcome.ACCESS_DENIED
    assert policy.classify_http_status(403) is ExecutionOutcome.ACCESS_DENIED
    # Unregistered: a 4xx that is NOT 401/403 is treated as a clean FAILED
    # only because no transient_response_statuses were declared for it --
    # this is still a deliberate per-status classification, not a blanket rule.
    assert policy.classify_http_status(400) is ExecutionOutcome.FAILED
    # 5xx is never a clean failure by default.
    assert policy.classify_http_status(503) is ExecutionOutcome.AMBIGUOUS


def test_429_is_not_hardcoded_as_failed_when_declared_transient():
    """Correction 8: a 4xx is never universally final. 429 is the classic
    counter-example -- a provider/operation can explicitly mark it as a
    transient (inconclusive) response instead of a terminal failure."""
    policy = ProviderOperationPolicy(
        provider="jira",
        operation="create_issue",
        transient_response_statuses=frozenset({429}),
    )
    assert policy.classify_http_status(429) is ExecutionOutcome.AMBIGUOUS
    # A different 4xx on the SAME policy, not declared transient, still FAILED.
    assert policy.classify_http_status(422) is ExecutionOutcome.FAILED


# ---------------------------------------------------------------------------
# Correction 5: HTTP-response retryability != safety of retrying the action
# ---------------------------------------------------------------------------


def test_classifying_a_status_as_ambiguous_grants_no_retry_permission_by_itself():
    """classify_http_status() returning AMBIGUOUS for a 429 must not, on
    its own, produce anything resembling permission to call
    create_object() again. The only thing that can ever grant that is a
    ReconciliationResult(outcome=CONFIRMED_ABSENT, absence_proof=...).
    This test asserts classify_http_status's return type carries no such
    capability -- it is a plain ExecutionOutcome enum member, nothing
    richer, nothing retry-shaped."""
    policy = ProviderOperationPolicy(
        provider="jira",
        operation="create_issue",
        transient_response_statuses=frozenset({429}),
    )
    outcome = policy.classify_http_status(429)
    assert outcome is ExecutionOutcome.AMBIGUOUS
    assert isinstance(outcome, ExecutionOutcome)
    # No retry-permission attribute/flag exists anywhere on this value.
    assert not hasattr(outcome, "retry_allowed")
    assert not hasattr(outcome, "safe_to_retry")


def test_ambiguous_result_from_a_transient_status_still_requires_full_reconciliation():
    """An AMBIGUOUS ExecutionResult is handled identically regardless of
    WHICH status produced it (429 vs 503 vs a network error) -- there is
    no shortcut path for '429 means just retry'. The only valid next step
    encoded in this module's types is a find_by_reference() call, whose
    result must independently satisfy CONFIRMED_ABSENT's own
    AbsenceProof gate before any retry."""
    policy = ProviderOperationPolicy(
        provider="jira", operation="create_issue", transient_response_statuses=frozenset({429})
    )
    result = ExecutionResult(
        outcome=policy.classify_http_status(429), http_status=429
    )
    assert result.outcome is ExecutionOutcome.AMBIGUOUS
    assert result.external_object_id is None
    # The ONLY path to a retry-eligible state is through ReconciliationResult;
    # an insufficient/absent proof keeps it blocked, exactly like any other
    # ambiguous cause (timeout, 5xx, connection reset).
    with pytest.raises(ValueError):
        ReconciliationResult(outcome=ReconciliationOutcome.CONFIRMED_ABSENT)


def test_2xx_classifies_as_confirmed_shaped_but_caller_must_still_supply_object_id():
    policy = ProviderOperationPolicy(provider="jira", operation="create_issue")
    assert policy.classify_http_status(201) is ExecutionOutcome.CONFIRMED
    # classify_http_status alone can never construct a valid CONFIRMED
    # ExecutionResult -- the invariant above still requires an explicit id.
    with pytest.raises(ValueError):
        ExecutionResult(outcome=policy.classify_http_status(201))


def test_registry_register_and_get_round_trip():
    registry = ProviderPolicyRegistry()
    policy = ProviderOperationPolicy(
        provider="jira", operation="create_issue", supports_absence_proof=True
    )
    registry.register(policy)
    fetched = registry.get("jira", "create_issue")
    assert fetched.supports_absence_proof is True
    # A different operation on the same provider is untouched by the
    # registration above and still gets the safe default.
    other = registry.get("jira", "update_issue")
    assert other.supports_absence_proof is False
