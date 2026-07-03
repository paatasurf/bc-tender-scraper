"""Deterministic google_enrichment_status state machine."""

from __future__ import annotations

from pipeline.google_enrichment.constants import (
    enriched,
    error,
    no_match,
    pending,
    review,
    stale,
)

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    pending: frozenset({enriched, review, no_match, error}),
    review: frozenset({enriched, no_match}),
    enriched: frozenset({stale}),
    stale: frozenset({enriched, review, no_match, error}),
    error: frozenset({pending}),
}


class InvalidStateTransitionError(ValueError):
    """Raised when a google_enrichment_status transition is not permitted."""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid google_enrichment_status transition: {from_status!r} -> {to_status!r}"
        )


def allowed_transitions(from_status: str) -> frozenset[str]:
    return VALID_TRANSITIONS.get(from_status, frozenset())


def transition(from_status: str, to_status: str) -> str:
    """Return to_status after validating the transition. Raises on invalid moves."""
    if to_status not in allowed_transitions(from_status):
        raise InvalidStateTransitionError(from_status, to_status)
    return to_status


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in allowed_transitions(from_status)
