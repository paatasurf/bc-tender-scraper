from __future__ import annotations

import pytest

from pipeline.registry_engine.derived_tender_evidence.evaluate import (
    DerivedTenderEvidenceEvaluationError,
    evaluate_derived_tender_evidence_payload,
)

HASH = "a" * 64


def _path_a(**overrides):
    result = {
        "schema_version": 1,
        "inventory_total": 10,
        "eligible_awarded_total": 2,
        "awarded_with_award_id": 2,
        "awarded_without_award_id": 0,
        "non_awarded_with_award_id": 0,
        "dangling_award_id_count": 0,
        "award_without_company_count": 0,
        "resolved_award_company_count": 2,
        "resolved_awarded_winner_count": 2,
        "shared_award_id_count": 0,
        "shared_award_id_tender_count": 0,
        "match_confidence_distribution": {"missing": 0, "partial": 0, "high": 2},
        "entity_role_counts": {"canonical": 2},
        "dataset_hash": HASH,
    }
    result.update(overrides)
    return result


def _path_b(**overrides):
    result = {
        "schema_version": 1,
        "inventory_total": 10,
        "tenders_with_valid_external_id": 10,
        "tenders_missing_external_id": 0,
        "ambiguous_external_id_tender_count": 0,
        "ambiguous_external_id_distinct_count": 0,
        "safely_attributable_tenders": 10,
        "tenders_with_reported_bidders": 1,
        "bidder_count_distribution": {"1": 1, "2": 0, "3_plus": 0},
        "outcomes_breakdown": {"won": 1},
        "dangling_company_id_count": 0,
        "entity_role_counts": {"canonical": 1},
        "dataset_hash": HASH,
    }
    result.update(overrides)
    return result


def _cross_path(**overrides):
    result = {
        "schema_version": 1,
        "comparable_tender_count": 1,
        "ambiguous_excluded_count": 0,
        "same_winner_confirmed_won": 1,
        "different_winner": 0,
        "winner_marked_lost": 0,
        "winner_marked_withdrawn": 0,
        "winner_marked_pending": 0,
    }
    result.update(overrides)
    return result


def _payload(*, path_a=None, path_b=None, cross_path=None, schema_version=1):
    return {
        "schema_version": schema_version,
        "path_a": path_a or _path_a(),
        "path_b": path_b or _path_b(),
        "cross_path": cross_path or _cross_path(),
    }


def test_fully_clean_payload_passes():
    scorecard = evaluate_derived_tender_evidence_payload(_payload())

    assert scorecard["overall_status"] == "PASS"
    assert scorecard["path_a"]["failures"] == []
    assert scorecard["path_b"]["failures"] == []
    assert scorecard["cross_path"]["blocked"] == []
    assert scorecard["tenders_company_id_created"] is False


def test_dangling_award_id_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_a=_path_a(dangling_award_id_count=1))
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "DANGLING_AWARD_ID" in scorecard["path_a"]["failures"]


def test_dangling_company_id_path_b_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_b=_path_b(dangling_company_id_count=1))
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "DANGLING_COMPANY_ID_PATH_B" in scorecard["path_b"]["failures"]


def test_path_a_award_id_partition_invariant_violation_fails():
    """dangling + award_without_company + resolved_award_company_count
    (all-status) must equal awarded_with_award_id + non_awarded_with_award_id.
    The partition uses the all-status field, not the awarded-only one."""
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_a=_path_a(resolved_award_company_count=999))
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_A_AWARD_ID_PARTITION_INCONSISTENT" in scorecard["path_a"]["failures"]


def test_coverage_never_exceeds_one_given_correct_invariants():
    """resolved_awarded_winner_count (awarded-only, the coverage numerator)
    must never exceed eligible_awarded_total (the coverage denominator) —
    otherwise coverage_rate would exceed 1, which can only happen if the
    reported counts are internally inconsistent. This must FAIL, not
    silently produce a coverage_rate > 1."""
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(
                eligible_awarded_total=2,
                resolved_awarded_winner_count=3,
                resolved_award_company_count=3,
            )
        )
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_A_WINNER_COUNT_EXCEEDS_ELIGIBLE" in scorecard["path_a"]["failures"]


def test_non_awarded_resolved_award_does_not_inflate_winner_coverage():
    """A non-awarded tender with a resolved award link contributes to
    resolved_award_company_count (all-status) but must NOT contribute to
    resolved_awarded_winner_count (awarded-only, the coverage numerator).
    A clean payload where the two differ for exactly this reason must not
    itself be flagged as inconsistent."""
    path_a = _path_a(
        non_awarded_with_award_id=1,
        resolved_award_company_count=3,  # 2 awarded + 1 non-awarded, all resolved
        resolved_awarded_winner_count=2,  # only the 2 awarded ones count as coverage
        match_confidence_distribution={"missing": 0, "partial": 0, "high": 3},
    )

    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_a=path_a))

    assert (
        "PATH_A_AWARD_ID_PARTITION_INCONSISTENT" not in scorecard["path_a"]["failures"]
    )
    assert (
        "PATH_A_WINNER_COUNT_EXCEEDS_RESOLVED_AWARDS"
        not in scorecard["path_a"]["failures"]
    )
    assert scorecard["path_a"]["coverage_rate"] == 1.0


def test_path_a_confidence_distribution_invariant_violation_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(
                match_confidence_distribution={"missing": 0, "partial": 0, "high": 999}
            )
        )
    )

    assert scorecard["overall_status"] == "FAIL"
    assert (
        "PATH_A_CONFIDENCE_DISTRIBUTION_INCONSISTENT" in scorecard["path_a"]["failures"]
    )


def test_path_b_attributable_total_invariant_violation_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_b=_path_b(safely_attributable_tenders=999))
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_B_ATTRIBUTABLE_TOTAL_INCONSISTENT" in scorecard["path_b"]["failures"]


def test_path_b_bidder_distribution_invariant_violation_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_b=_path_b(tenders_with_reported_bidders=999))
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_B_BIDDER_DISTRIBUTION_INCONSISTENT" in scorecard["path_b"]["failures"]


def test_ambiguous_external_tender_id_blocks():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_b=_path_b(
                tenders_with_valid_external_id=10,
                ambiguous_external_id_tender_count=2,
                ambiguous_external_id_distinct_count=1,
                safely_attributable_tenders=8,
            )
        )
    )

    assert scorecard["overall_status"] == "BLOCKED"
    assert "AMBIGUOUS_EXTERNAL_TENDER_ID" in scorecard["path_b"]["blocked"]


@pytest.mark.parametrize(
    "field, code",
    [
        ("different_winner", "CROSS_PATH_DIFFERENT_WINNER"),
        ("winner_marked_lost", "CROSS_PATH_WINNER_MARKED_LOST"),
        ("winner_marked_withdrawn", "CROSS_PATH_WINNER_MARKED_WITHDRAWN"),
    ],
)
def test_each_cross_path_contradiction_blocks(field, code):
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(cross_path=_cross_path(**{field: 1}))
    )

    assert scorecard["overall_status"] == "BLOCKED"
    assert code in scorecard["cross_path"]["blocked"]


def test_winner_marked_pending_does_not_block():
    """A pending self-report is not yet a contradiction — the overall
    status must not become BLOCKED (or FAIL/WARN) from this alone."""
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(cross_path=_cross_path(winner_marked_pending=1))
    )

    assert scorecard["overall_status"] == "PASS"
    assert scorecard["cross_path"]["status"] == "PASS"
    assert scorecard["cross_path"]["blocked"] == []
    assert scorecard["cross_path"]["winner_marked_pending"] == 1


def test_path_a_non_awarded_with_award_id_warns():
    # total_with_award_id = awarded_with_award_id + non_awarded_with_award_id
    # must equal dangling + award_without_company +
    # resolved_award_company_count (all-status) for the partition invariant
    # to hold — resolved_awarded_winner_count (awarded-only) is unrelated
    # and stays at its base value.
    path_a = _path_a(
        non_awarded_with_award_id=1,
        resolved_award_company_count=3,
        match_confidence_distribution={"missing": 0, "partial": 0, "high": 3},
    )

    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_a=path_a))

    assert scorecard["overall_status"] == "WARN"
    assert "NON_AWARDED_WITH_AWARD_ID" in scorecard["path_a"]["warnings"]


def test_path_a_shared_award_ids_warns():
    path_a = _path_a(shared_award_id_count=1, shared_award_id_tender_count=2)

    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_a=path_a))

    assert scorecard["overall_status"] == "WARN"
    assert "SHARED_AWARD_IDS" in scorecard["path_a"]["warnings"]


def test_path_a_awarded_without_award_id_warns():
    path_a = _path_a(
        eligible_awarded_total=3,
        awarded_with_award_id=2,
        awarded_without_award_id=1,
    )
    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_a=path_a))

    assert scorecard["overall_status"] == "WARN"
    assert "AWARDED_WITHOUT_AWARD_ID" in scorecard["path_a"]["warnings"]


def test_path_a_award_without_company_warns():
    path_a = _path_a(
        award_without_company_count=1,
        resolved_award_company_count=1,
        resolved_awarded_winner_count=1,
        entity_role_counts={"canonical": 1},
    )
    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_a=path_a))

    assert scorecard["overall_status"] == "WARN"
    assert "AWARD_WITHOUT_COMPANY" in scorecard["path_a"]["warnings"]


def test_path_b_missing_external_id_warns():
    path_b = _path_b(
        tenders_with_valid_external_id=9,
        tenders_missing_external_id=1,
        inventory_total=10,
        safely_attributable_tenders=9,
    )
    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_b=path_b))

    assert scorecard["overall_status"] == "WARN"
    assert "MISSING_EXTERNAL_TENDER_ID" in scorecard["path_b"]["warnings"]


def test_fail_outranks_warn_and_blocked():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(dangling_award_id_count=1, shared_award_id_count=1),
            cross_path=_cross_path(different_winner=1),
        )
    )

    assert scorecard["overall_status"] == "FAIL"


def test_blocked_outranks_warn():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(shared_award_id_count=1, shared_award_id_tender_count=2),
            cross_path=_cross_path(different_winner=1),
        )
    )

    assert scorecard["overall_status"] == "BLOCKED"


def test_malformed_payload_missing_section_raises():
    with pytest.raises(DerivedTenderEvidenceEvaluationError):
        evaluate_derived_tender_evidence_payload({"path_a": _path_a()})


def test_malformed_field_type_raises():
    bad = _path_a()
    bad["inventory_total"] = "10"  # not an int
    with pytest.raises(DerivedTenderEvidenceEvaluationError):
        evaluate_derived_tender_evidence_payload(_payload(path_a=bad))


def test_negative_count_raises():
    bad = _path_a()
    bad["inventory_total"] = -1
    with pytest.raises(DerivedTenderEvidenceEvaluationError):
        evaluate_derived_tender_evidence_payload(_payload(path_a=bad))


def test_bool_field_rejected_as_not_an_integer():
    """bool is a subclass of int in Python — must be explicitly excluded."""
    bad = _path_a()
    bad["inventory_total"] = True
    with pytest.raises(DerivedTenderEvidenceEvaluationError):
        evaluate_derived_tender_evidence_payload(_payload(path_a=bad))


# --- schema_version: missing, bool, string, 0, 2 — all rejected ----------


@pytest.mark.parametrize(
    "bad_value, present",
    [
        (None, False),  # key entirely missing
        (True, True),
        ("1", True),
        (0, True),
        (2, True),
    ],
)
def test_path_a_schema_version_invalid_values_raise(bad_value, present):
    bad = _path_a()
    if present:
        bad["schema_version"] = bad_value
    else:
        del bad["schema_version"]
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(path_a=bad))


def test_path_b_schema_version_missing_raises():
    bad = _path_b()
    del bad["schema_version"]
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(path_b=bad))


def test_path_b_schema_version_wrong_value_raises():
    bad = _path_b(schema_version=2)
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(path_b=bad))


def test_cross_path_schema_version_missing_raises():
    bad = _cross_path()
    del bad["schema_version"]
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(cross_path=bad))


def test_cross_path_schema_version_wrong_value_raises():
    bad = _cross_path(schema_version=0)
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(cross_path=bad))


def test_top_level_schema_version_missing_raises():
    payload = _payload()
    del payload["schema_version"]
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(payload)


def test_top_level_schema_version_bool_raises():
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(schema_version=True))


def test_top_level_schema_version_string_raises():
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(schema_version="1"))


def test_top_level_schema_version_zero_raises():
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(schema_version=0))


def test_top_level_schema_version_two_raises():
    with pytest.raises(DerivedTenderEvidenceEvaluationError, match="schema_version"):
        evaluate_derived_tender_evidence_payload(_payload(schema_version=2))


def test_valid_schema_version_one_passes():
    scorecard = evaluate_derived_tender_evidence_payload(_payload(schema_version=1))
    assert scorecard["overall_status"] == "PASS"


# --- dataset_hash: lowercase SHA-256 format ------------------------------


def test_path_a_dataset_hash_missing_fails():
    bad = _path_a()
    del bad["dataset_hash"]
    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_a=bad))
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_A_INVALID_DATASET_HASH" in scorecard["path_a"]["failures"]


def test_path_a_dataset_hash_uppercase_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_a=_path_a(dataset_hash="A" * 64))
    )
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_A_INVALID_DATASET_HASH" in scorecard["path_a"]["failures"]


def test_path_a_dataset_hash_wrong_length_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_a=_path_a(dataset_hash="a" * 63))
    )
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_A_INVALID_DATASET_HASH" in scorecard["path_a"]["failures"]


def test_path_b_dataset_hash_missing_fails():
    bad = _path_b()
    del bad["dataset_hash"]
    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_b=bad))
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_B_INVALID_DATASET_HASH" in scorecard["path_b"]["failures"]


def test_path_b_dataset_hash_non_hex_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_b=_path_b(dataset_hash="g" * 64))
    )
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_B_INVALID_DATASET_HASH" in scorecard["path_b"]["failures"]


def test_valid_dataset_hash_passes():
    scorecard = evaluate_derived_tender_evidence_payload(_payload())
    assert "PATH_A_INVALID_DATASET_HASH" not in scorecard["path_a"]["failures"]
    assert "PATH_B_INVALID_DATASET_HASH" not in scorecard["path_b"]["failures"]


# --- Path A entity-role-count invariant -----------------------------------


def test_path_a_entity_role_count_mismatch_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_a=_path_a(entity_role_counts={"canonical": 999}))
    )
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_A_ENTITY_ROLE_COUNT_INCONSISTENT" in scorecard["path_a"]["failures"]


def test_path_a_entity_role_count_match_passes():
    scorecard = evaluate_derived_tender_evidence_payload(_payload())
    assert (
        "PATH_A_ENTITY_ROLE_COUNT_INCONSISTENT" not in scorecard["path_a"]["failures"]
    )


# --- Path B entity-role + outcomes invariant ------------------------------


def test_path_b_entity_role_count_mismatch_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(path_b=_path_b(outcomes_breakdown={"won": 999}))
    )
    assert scorecard["overall_status"] == "FAIL"
    assert "PATH_B_ENTITY_ROLE_COUNT_INCONSISTENT" in scorecard["path_b"]["failures"]


def test_path_b_entity_role_count_includes_dangling_and_passes():
    """entity_role_counts (resolved) + dangling_company_id_count must equal
    outcomes_breakdown (all attributable rows) — a dangling row is still
    accounted for, just on the other side of the sum."""
    path_b = _path_b(
        dangling_company_id_count=1,
        outcomes_breakdown={"won": 2},  # 1 resolved (entity_role_counts) + 1 dangling
    )
    scorecard = evaluate_derived_tender_evidence_payload(_payload(path_b=path_b))
    assert (
        "PATH_B_ENTITY_ROLE_COUNT_INCONSISTENT" not in scorecard["path_b"]["failures"]
    )


# --- shared-award-id invariants -------------------------------------------


def test_shared_award_id_nonzero_count_but_zero_tender_count_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(shared_award_id_count=1, shared_award_id_tender_count=0)
        )
    )
    assert scorecard["overall_status"] == "FAIL"
    assert (
        "PATH_A_SHARED_AWARD_ID_COUNT_INCONSISTENT" in scorecard["path_a"]["failures"]
    )


def test_shared_award_id_tender_count_below_double_fails():
    """Each shared award_id is shared by >= 2 tenders by definition, so the
    tender count can never be less than 2x the distinct shared-id count."""
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(shared_award_id_count=2, shared_award_id_tender_count=3)
        )
    )
    assert scorecard["overall_status"] == "FAIL"
    assert (
        "PATH_A_SHARED_AWARD_ID_COUNT_INCONSISTENT" in scorecard["path_a"]["failures"]
    )


def test_shared_award_id_consistent_values_pass():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_a=_path_a(shared_award_id_count=2, shared_award_id_tender_count=5)
        )
    )
    assert (
        "PATH_A_SHARED_AWARD_ID_COUNT_INCONSISTENT"
        not in scorecard["path_a"]["failures"]
    )


# --- ambiguous-external-tender-id invariants ------------------------------


def test_ambiguous_external_id_nonzero_distinct_but_zero_tender_count_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_b=_path_b(
                ambiguous_external_id_distinct_count=1,
                ambiguous_external_id_tender_count=0,
            )
        )
    )
    assert scorecard["overall_status"] == "FAIL"
    assert (
        "PATH_B_AMBIGUOUS_EXTERNAL_ID_COUNT_INCONSISTENT"
        in scorecard["path_b"]["failures"]
    )


def test_ambiguous_external_id_tender_count_below_double_fails():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_b=_path_b(
                ambiguous_external_id_distinct_count=2,
                ambiguous_external_id_tender_count=3,
            )
        )
    )
    assert scorecard["overall_status"] == "FAIL"
    assert (
        "PATH_B_AMBIGUOUS_EXTERNAL_ID_COUNT_INCONSISTENT"
        in scorecard["path_b"]["failures"]
    )


def test_ambiguous_external_id_consistent_values_pass():
    scorecard = evaluate_derived_tender_evidence_payload(
        _payload(
            path_b=_path_b(
                ambiguous_external_id_distinct_count=2,
                ambiguous_external_id_tender_count=4,
                tenders_with_valid_external_id=10,
                safely_attributable_tenders=6,
            )
        )
    )
    assert (
        "PATH_B_AMBIGUOUS_EXTERNAL_ID_COUNT_INCONSISTENT"
        not in scorecard["path_b"]["failures"]
    )


# --- valid artifact regression --------------------------------------------


def test_fully_valid_artifact_passes_every_new_invariant():
    """A single, fully self-consistent artifact satisfying every check added
    in this round (schema_version, dataset_hash format, entity-role sums,
    shared-award and ambiguous-id consistency) end to end."""
    scorecard = evaluate_derived_tender_evidence_payload(_payload())

    assert scorecard["overall_status"] == "PASS"
    assert scorecard["path_a"]["failures"] == []
    assert scorecard["path_b"]["failures"] == []
    assert scorecard["path_b"]["blocked"] == []
    assert scorecard["cross_path"]["blocked"] == []
