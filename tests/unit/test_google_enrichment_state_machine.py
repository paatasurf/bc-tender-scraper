"""Unit tests for google_enrichment_status state machine."""

from __future__ import annotations

import pytest

from pipeline.google_enrichment.constants import (
    enriched,
    error,
    no_match,
    pending,
    review,
    stale,
)
from pipeline.google_enrichment.state_machine import (
    InvalidStateTransitionError,
    allowed_transitions,
    can_transition,
    transition,
)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (pending, enriched),
        (pending, review),
        (pending, no_match),
        (pending, error),
        (review, enriched),
        (review, no_match),
        (enriched, stale),
        (stale, enriched),
        (stale, review),
        (stale, no_match),
        (stale, error),
        (error, pending),
    ],
)
def test_valid_transitions(from_status: str, to_status: str):
    assert can_transition(from_status, to_status)
    assert transition(from_status, to_status) == to_status


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (enriched, enriched),
        (enriched, pending),
        (enriched, review),
        (enriched, no_match),
        (enriched, error),
        (pending, pending),
        (pending, stale),
        (review, pending),
        (review, stale),
        (review, error),
        (no_match, enriched),
        (no_match, pending),
        (stale, pending),
        (stale, stale),
        (error, enriched),
        (error, error),
    ],
)
def test_invalid_transitions_raise(from_status: str, to_status: str):
    assert not can_transition(from_status, to_status)
    with pytest.raises(InvalidStateTransitionError) as exc:
        transition(from_status, to_status)
    assert exc.value.from_status == from_status
    assert exc.value.to_status == to_status


def test_allowed_transitions_match_spec():
    assert allowed_transitions(pending) == frozenset({enriched, review, no_match, error})
    assert allowed_transitions(review) == frozenset({enriched, no_match})
    assert allowed_transitions(enriched) == frozenset({stale})
    assert allowed_transitions(stale) == frozenset({enriched, review, no_match, error})
    assert allowed_transitions(error) == frozenset({pending})
