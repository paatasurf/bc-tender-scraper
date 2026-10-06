"""IntegrationConnector contract (AI-Workforce PR1).

No implementation here calls a real network, Nango, or any external
system. This module defines only the shape every future connector
(Nango-backed or otherwise) must satisfy. Authorization (is this
connection active? does it belong to the caller's company? is the target
within allowed_projects?) is explicitly NOT this Protocol's job -- that
check belongs to whatever code looks up the IntegrationConnection row
before calling either method here, which in this PR is nothing yet (the
executor/actions layer is the next PR).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pipeline.integrations.results import ExecutionResult, ReconciliationResult


@runtime_checkable
class IntegrationConnector(Protocol):
    def create_object(
        self,
        *,
        connection_id: int,
        operation: str,
        idempotency_key: str,
        payload: dict,
    ) -> ExecutionResult:
        """Attempt to create exactly one external object.

        `connection_id` is an IntegrationConnection.id (this repo's own
        primary key) -- never a Nango connection_id or any other
        provider-side value. `idempotency_key` is supplied by the caller
        and must be stable across retries of the SAME logical attempt (the
        future executor PR is responsible for that stability, not this
        contract). This method does not deduplicate on its own -- calling
        it twice with the same idempotency_key for a provider that doesn't
        support idempotent creates may create two objects; preventing that
        is the executor's atomic-claim responsibility, not the
        connector's.
        """
        ...

    def find_by_reference(
        self,
        *,
        connection_id: int,
        operation: str,
        idempotency_key: str,
    ) -> ReconciliationResult:
        """Ask whether the object for this idempotency_key actually
        exists in the target system -- used only after an AMBIGUOUS
        ExecutionResult. Implementations must return INCONCLUSIVE rather
        than guessing CONFIRMED_ABSENT unless the registered
        ProviderOperationPolicy for (provider, operation) explicitly
        marks this exact lookup as absence-provable, AND the
        implementation can construct a sufficient AbsenceProof (strong
        read consistency, fully paginated search, confirmed scope, no
        error) -- ReconciliationResult's own constructor enforces this,
        so an implementation cannot bypass it by accident.

        A CONFIRMED_ABSENT result is the ONLY thing that may ever
        authorize a caller to call create_object() again for the same
        logical attempt. An AMBIGUOUS ExecutionResult being caused by a
        "transient" HTTP status (e.g. 429) does NOT by itself authorize a
        retry -- that classification only describes the response, not
        the safety of re-attempting the action.
        """
        ...
