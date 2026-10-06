"""Pure unit tests for the IntegrationConnector Protocol -- no network, no
Nango. Uses an in-memory fake purely to prove the contract shape is
implementable and usable; this fake is test infrastructure only and must
never be imported from application code.
"""

from __future__ import annotations

from pipeline.integrations.connector import IntegrationConnector
from pipeline.integrations.results import (
    ExecutionOutcome,
    ExecutionResult,
    ReconciliationOutcome,
    ReconciliationResult,
)


class _FakeConnector:
    """In-memory stand-in satisfying IntegrationConnector -- test-only."""

    def __init__(self) -> None:
        self.created: dict[str, str] = {}  # idempotency_key -> external_object_id
        self.calls: list[tuple[str, dict]] = []

    def create_object(
        self, *, connection_id: int, operation: str, idempotency_key: str, payload: dict
    ) -> ExecutionResult:
        self.calls.append(("create_object", {"idempotency_key": idempotency_key}))
        if idempotency_key in self.created:
            return ExecutionResult(
                outcome=ExecutionOutcome.CONFIRMED,
                external_object_id=self.created[idempotency_key],
            )
        external_id = f"FAKE-{len(self.created) + 1}"
        self.created[idempotency_key] = external_id
        return ExecutionResult(outcome=ExecutionOutcome.CONFIRMED, external_object_id=external_id)

    def find_by_reference(
        self, *, connection_id: int, operation: str, idempotency_key: str
    ) -> ReconciliationResult:
        self.calls.append(("find_by_reference", {"idempotency_key": idempotency_key}))
        if idempotency_key in self.created:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.CONFIRMED_PRESENT,
                external_object_id=self.created[idempotency_key],
            )
        return ReconciliationResult(outcome=ReconciliationOutcome.INCONCLUSIVE)


def test_fake_connector_satisfies_the_protocol_at_runtime():
    fake = _FakeConnector()
    assert isinstance(fake, IntegrationConnector)


def test_create_object_returns_confirmed_with_object_id():
    fake = _FakeConnector()
    result = fake.create_object(
        connection_id=1, operation="create_task", idempotency_key="k1", payload={}
    )
    assert result.outcome is ExecutionOutcome.CONFIRMED
    assert result.external_object_id == "FAKE-1"


def test_find_by_reference_before_any_create_is_inconclusive_not_absent():
    """Even a fake, fully-controlled connector must not claim
    CONFIRMED_ABSENT just because nothing is in its own in-memory store --
    the contract's default posture is INCONCLUSIVE unless a connector has
    a specific, justified reason to claim absence."""
    fake = _FakeConnector()
    result = fake.find_by_reference(connection_id=1, operation="create_task", idempotency_key="never-created")
    assert result.outcome is ReconciliationOutcome.INCONCLUSIVE
    assert result.external_object_id is None


def test_find_by_reference_after_create_confirms_present():
    fake = _FakeConnector()
    created = fake.create_object(
        connection_id=1, operation="create_task", idempotency_key="k2", payload={}
    )
    found = fake.find_by_reference(connection_id=1, operation="create_task", idempotency_key="k2")
    assert found.outcome is ReconciliationOutcome.CONFIRMED_PRESENT
    assert found.external_object_id == created.external_object_id


def test_protocol_does_not_perform_authorization_itself():
    """The Protocol takes a bare connection_id (int) and trusts the
    caller -- it has no company_id, no allowed_projects check, nothing.
    This test documents that boundary explicitly: authorization is out of
    scope for this contract by design, not by oversight."""
    import inspect

    sig = inspect.signature(IntegrationConnector.create_object)
    assert "connection_id" in sig.parameters
    assert "company_id" not in sig.parameters
