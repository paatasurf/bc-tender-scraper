"""Pure unit tests for pipeline.registry_engine.claims.consistency.

No database, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pipeline.registry_engine.claims.consistency import (
    ClaimRow,
    EventRow,
    EvidenceRow,
    RuleSetRow,
    evaluate_claims_consistency,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_VALID_HASH_A = "a" * 64
_VALID_HASH_B = "b" * 64


def _claim(**overrides) -> ClaimRow:
    kwargs = dict(
        claim_id="claim-1",
        company_id=1,
        claim_type="sector_classification",
        predicate="dominant_sector",
        source_type="licence_authority",
        primary_evidence_content_hash=_VALID_HASH_A,
        idempotency_key=_VALID_HASH_B,
        rule_set_version_id="rs-1",
        effective_at=_NOW,
    )
    kwargs.update(overrides)
    return ClaimRow(**kwargs)


def _evidence(**overrides) -> EvidenceRow:
    kwargs = dict(
        claim_evidence_id="evidence-1",
        claim_id="claim-1",
        evidence_source="licence_authority_raw",
        content_hash=_VALID_HASH_A,
    )
    kwargs.update(overrides)
    return EvidenceRow(**kwargs)


def _event(**overrides) -> EventRow:
    kwargs = dict(
        event_id="event-1",
        claim_id="claim-1",
        event_type="rejected",
        related_claim_id=None,
        rule_set_version_id="rs-1",
        event_at=_NOW,
    )
    kwargs.update(overrides)
    return EventRow(**kwargs)


def _rule_set(**overrides) -> RuleSetRow:
    kwargs = dict(
        rule_set_version_id="rs-1",
        claim_type="sector_classification",
        effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    kwargs.update(overrides)
    return RuleSetRow(**kwargs)


# --- baseline ---------------------------------------------------------------------


def test_empty_dataset_passes():
    result = evaluate_claims_consistency(
        claims=[], evidence=[], events=[], rule_sets=[]
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["counts"] == {"claims": 0, "evidence": 0, "events": 0, "rule_sets": 0}


def test_fully_consistent_dataset_passes():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["counts"]["claims"] == 1


def test_fully_consistent_dataset_with_event_passes():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[_event()],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []


def test_superseded_event_with_matching_scope_related_claim_passes():
    result = evaluate_claims_consistency(
        claims=[_claim(claim_id="claim-1"), _claim(claim_id="claim-2")],
        evidence=[
            _evidence(claim_evidence_id="e1", claim_id="claim-1"),
            _evidence(claim_evidence_id="e2", claim_id="claim-2"),
        ],
        events=[
            _event(
                event_id="event-1",
                claim_id="claim-1",
                event_type="superseded",
                related_claim_id="claim-2",
            )
        ],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "PASS"


# --- claim -> primary evidence hash ------------------------------------------------


def test_missing_primary_evidence_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()], evidence=[], events=[], rule_sets=[_rule_set()]
    )
    assert result["status"] == "FAIL"
    assert any("no claim_evidence row matches" in f for f in result["findings"])


def test_evidence_with_different_hash_does_not_satisfy_the_claim():
    result = evaluate_claims_consistency(
        claims=[_claim(primary_evidence_content_hash=_VALID_HASH_A)],
        evidence=[_evidence(content_hash=_VALID_HASH_B)],
        events=[],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("no claim_evidence row matches" in f for f in result["findings"])


def test_evidence_dangling_claim_id_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[],
        evidence=[_evidence(claim_id="does-not-exist")],
        events=[],
        rule_sets=[],
    )
    assert result["status"] == "FAIL"
    assert any("dangling claim_id" in f for f in result["findings"])


# --- hash formats -------------------------------------------------------------------


def test_invalid_idempotency_key_format_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim(idempotency_key="not-a-hash")],
        evidence=[_evidence()],
        events=[],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("invalid idempotency_key format" in f for f in result["findings"])


def test_invalid_primary_evidence_hash_format_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim(primary_evidence_content_hash="short")],
        evidence=[_evidence(content_hash="short")],
        events=[],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any(
        "invalid primary_evidence_content_hash format" in f for f in result["findings"]
    )


def test_invalid_evidence_content_hash_format_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence(content_hash="UPPERCASE" + "a" * 55)],
        events=[],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("invalid content_hash format" in f for f in result["findings"])


# --- terminal-event uniqueness -------------------------------------------------------


def test_duplicate_terminal_events_on_same_claim_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[
            _event(event_id="event-1"),
            _event(event_id="event-2"),
        ],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("terminal events" in f for f in result["findings"])


def test_event_dangling_claim_id_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[],
        evidence=[],
        events=[_event(claim_id="does-not-exist", rule_set_version_id="rs-1")],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("dangling claim_id" in f for f in result["findings"])


# --- related-claim scope --------------------------------------------------------------


def test_superseded_event_missing_related_claim_id_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[_event(event_type="superseded", related_claim_id=None)],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("missing related_claim_id" in f for f in result["findings"])


def test_superseded_event_dangling_related_claim_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[_event(event_type="superseded", related_claim_id="does-not-exist")],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any("dangling related_claim_id" in f for f in result["findings"])


def test_superseded_event_cross_scope_related_claim_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[
            _claim(claim_id="claim-1", predicate="dominant_sector"),
            _claim(claim_id="claim-2", predicate="primary_trade"),
        ],
        evidence=[
            _evidence(claim_evidence_id="e1", claim_id="claim-1"),
            _evidence(claim_evidence_id="e2", claim_id="claim-2"),
        ],
        events=[
            _event(
                event_id="event-1",
                claim_id="claim-1",
                event_type="superseded",
                related_claim_id="claim-2",
            )
        ],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any(
        "outside the company_id/claim_type/predicate scope" in f
        for f in result["findings"]
    )


# --- rule-set compatibility (claims and events) ----------------------------------------


def test_claim_dangling_rule_set_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim(rule_set_version_id="does-not-exist")],
        evidence=[_evidence()],
        events=[],
        rule_sets=[],
    )
    assert result["status"] == "FAIL"
    assert any("dangling rule_set_version_id" in f for f in result["findings"])


def test_claim_incompatible_claim_type_rule_set_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim(claim_type="sector_classification")],
        evidence=[_evidence()],
        events=[],
        rule_sets=[_rule_set(claim_type="licence_registration")],
    )
    assert result["status"] == "FAIL"
    assert any("does not match claim_type" in f for f in result["findings"])


def test_claim_future_rule_set_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim(effective_at=_NOW)],
        evidence=[_evidence()],
        events=[],
        rule_sets=[_rule_set(effective_from=datetime(2999, 1, 1, tzinfo=timezone.utc))],
    )
    assert result["status"] == "FAIL"
    assert any("not yet effective" in f for f in result["findings"])


def test_event_dangling_rule_set_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[_event(rule_set_version_id="does-not-exist")],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert any(
        "claim_events event-1: dangling rule_set_version_id" in f
        for f in result["findings"]
    )


def test_event_incompatible_claim_type_rule_set_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim(claim_type="sector_classification", rule_set_version_id="rs-1")],
        evidence=[_evidence()],
        events=[_event(rule_set_version_id="rs-2")],
        rule_sets=[
            _rule_set(rule_set_version_id="rs-1", claim_type="sector_classification"),
            _rule_set(rule_set_version_id="rs-2", claim_type="licence_registration"),
        ],
    )
    assert result["status"] == "FAIL"
    assert any(
        "does not match parent claim's claim_type" in f for f in result["findings"]
    )


def test_event_future_rule_set_is_a_finding():
    result = evaluate_claims_consistency(
        claims=[_claim()],
        evidence=[_evidence()],
        events=[_event(event_at=_NOW)],
        rule_sets=[_rule_set(effective_from=datetime(2999, 1, 1, tzinfo=timezone.utc))],
    )
    assert result["status"] == "FAIL"
    assert any("not yet effective at event_at" in f for f in result["findings"])


def test_isolated_violation_does_not_produce_unrelated_findings():
    """Damaging one claim must not report false findings about the other."""
    result = evaluate_claims_consistency(
        claims=[
            _claim(claim_id="claim-1", idempotency_key="not-a-hash"),
            _claim(claim_id="claim-2"),
        ],
        evidence=[
            _evidence(claim_evidence_id="e1", claim_id="claim-1"),
            _evidence(claim_evidence_id="e2", claim_id="claim-2"),
        ],
        events=[],
        rule_sets=[_rule_set()],
    )
    assert result["status"] == "FAIL"
    assert len(result["findings"]) == 1
    assert "claim-1" in result["findings"][0]
