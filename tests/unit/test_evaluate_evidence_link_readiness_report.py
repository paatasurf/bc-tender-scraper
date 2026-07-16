from __future__ import annotations

import json

import pytest

from pipeline.registry_engine.evidence.evaluate import (
    AuditEvaluationError,
    evaluate_audit_payload,
)
from scripts.evaluate_evidence_link_readiness_report import main

HASH = "a" * 64


def _source(**overrides):
    result = {
        "total_rows": 10,
        "rows_with_company_id": 8,
        "rows_without_company_id": 2,
        "orphan_count": 0,
        "non_canonical_count": 0,
        "linked_entity_role_counts": {"canonical": 8},
        "broken_redirect_count": 0,
        "cycle_count": 0,
        "depth_exhausted_count": 0,
        "excluded_target_count": 0,
        "dataset_hash": HASH,
    }
    result.update(overrides)
    return result


def _source_v1(**overrides):
    """A pre-breakdown Stage 2A artifact: no linked_entity_role_counts key at all."""
    result = _source(**overrides)
    result.pop("linked_entity_role_counts", None)
    return result


def _payload(*, permits=None, awards=None, schema_gap=True):
    return {
        "permits": permits or _source(),
        "contract_awards": awards or _source(),
        "tenders": {
            "total_tenders": 20,
            "has_company_id_column": not schema_gap,
            "schema_gap": schema_gap,
            "note": "diagnostic only",
        },
    }


def test_expected_tender_gap_blocks_otherwise_clean_baseline():
    scorecard = evaluate_audit_payload(_payload())

    assert scorecard["overall_status"] == "BLOCKED"
    assert scorecard["sources"]["permits"]["status"] == "WARN"
    assert scorecard["sources"]["permits"]["raw_linkage_rate"] == 0.8
    assert scorecard["tenders"]["blockers"] == ["TENDER_COMPANY_ID_SCHEMA_GAP"]
    assert scorecard["customer_accuracy_claim_supported"] is False


@pytest.mark.parametrize(
    "field",
    [
        "orphan_count",
        "broken_redirect_count",
        "cycle_count",
        "depth_exhausted_count",
        "excluded_target_count",
    ],
)
def test_each_structural_defect_fails(field):
    scorecard = evaluate_audit_payload(
        _payload(permits=_source(**{field: 1}), schema_gap=False)
    )

    assert scorecard["overall_status"] == "FAIL"
    assert field.upper() in scorecard["sources"]["permits"]["failures"]


def test_non_canonical_links_warn_without_claiming_corruption():
    scorecard = evaluate_audit_payload(
        _payload(
            permits=_source(
                rows_with_company_id=10,
                rows_without_company_id=0,
                non_canonical_count=2,
                linked_entity_role_counts={"canonical": 8, "standalone": 2},
            ),
            awards=_source(
                rows_with_company_id=10,
                rows_without_company_id=0,
                linked_entity_role_counts={"canonical": 10},
            ),
            schema_gap=False,
        )
    )

    assert scorecard["overall_status"] == "WARN"
    assert scorecard["sources"]["permits"]["failures"] == []
    assert "NON_CANONICAL_DIRECT_LINKS" in scorecard["sources"]["permits"]["warnings"]


def test_fully_clean_payload_passes_but_does_not_support_accuracy_claim():
    clean = _source(
        rows_with_company_id=10,
        rows_without_company_id=0,
        linked_entity_role_counts={"canonical": 10},
    )
    scorecard = evaluate_audit_payload(
        _payload(permits=clean, awards=clean, schema_gap=False)
    )

    assert scorecard["overall_status"] == "PASS"
    assert scorecard["customer_accuracy_claim_supported"] is False


def test_inconsistent_counts_and_invalid_hash_fail():
    scorecard = evaluate_audit_payload(
        _payload(
            permits=_source(rows_with_company_id=7, rows_without_company_id=2),
            awards=_source(dataset_hash="bad"),
            schema_gap=False,
        )
    )

    assert scorecard["overall_status"] == "FAIL"
    assert "ROW_COUNT_INCONSISTENT" in scorecard["sources"]["permits"]["failures"]
    assert "INVALID_DATASET_HASH" in scorecard["sources"]["contract_awards"]["failures"]


def test_malformed_tender_contract_is_rejected():
    with pytest.raises(AuditEvaluationError, match="inverse"):
        payload = _payload(schema_gap=True)
        payload["tenders"]["has_company_id_column"] = True
        evaluate_audit_payload(payload)


def test_cli_outputs_json_and_uses_blocked_exit_code(tmp_path, capsys):
    artifact = tmp_path / "audit.json"
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")

    exit_code = main([str(artifact)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["overall_status"] == "BLOCKED"


def test_cli_rejects_invalid_json(tmp_path, capsys):
    artifact = tmp_path / "audit.json"
    artifact.write_text("not json", encoding="utf-8")

    exit_code = main([str(artifact)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["overall_status"] == "FAIL"


# --- v1/v2 schema compatibility ------------------------------------------
#
# v1 artifacts predate the linked_entity_role_counts breakdown and never
# carried the key at all. A v2 evaluator must keep reading them — not treat
# their absence as malformed input — while still fully validating everything
# a v1 artifact ever had (row counts, structural counts, dataset_hash).


def test_v1_payload_without_role_counts_is_still_readable_and_clean():
    clean_v1 = _source_v1(rows_with_company_id=10, rows_without_company_id=0)
    scorecard = evaluate_audit_payload(
        _payload(permits=clean_v1, awards=clean_v1, schema_gap=False)
    )

    assert scorecard["overall_status"] == "PASS"
    permits = scorecard["sources"]["permits"]
    assert permits["failures"] == []
    assert permits["linked_entity_role_counts"] is None
    assert permits["schema_version"] == 1


def test_v1_payload_still_flags_row_count_inconsistency():
    broken_v1 = _source_v1(rows_with_company_id=7, rows_without_company_id=2)
    scorecard = evaluate_audit_payload(
        _payload(permits=broken_v1, awards=_source_v1(), schema_gap=False)
    )

    assert scorecard["overall_status"] == "FAIL"
    failures = scorecard["sources"]["permits"]["failures"]
    assert "ROW_COUNT_INCONSISTENT" in failures
    # v1 has no role-count field at all, so the v2-only invariants cannot apply.
    assert "ENTITY_ROLE_COUNT_INCONSISTENT" not in failures
    assert "MISSING_COMPANY_ROLE_COUNT_INCONSISTENT" not in failures


def test_v1_payload_with_orphans_does_not_require_missing_company_role():
    """v1 never recorded which role an orphan's target had, so orphan_count
    alone (not a role-count cross-check) must still drive the FAIL."""
    v1_with_orphan = _source_v1(orphan_count=1)
    scorecard = evaluate_audit_payload(
        _payload(permits=v1_with_orphan, awards=_source_v1(), schema_gap=False)
    )

    assert scorecard["overall_status"] == "FAIL"
    failures = scorecard["sources"]["permits"]["failures"]
    assert "ORPHAN_COUNT" in failures
    assert "MISSING_COMPANY_ROLE_COUNT_INCONSISTENT" not in failures


def test_v2_payload_without_explicit_schema_version_is_still_fully_validated():
    """A v2 payload is recognized by the presence of linked_entity_role_counts,
    not by requiring every producer to remember to stamp schema_version."""
    mismatched = _source(linked_entity_role_counts={"canonical": 3})  # sums to 3, not 8
    scorecard = evaluate_audit_payload(
        _payload(permits=mismatched, awards=_source(), schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert permits["schema_version"] == 2
    assert "ENTITY_ROLE_COUNT_INCONSISTENT" in permits["failures"]


def test_v2_payload_missing_company_role_mismatch_fails():
    mismatched = _source(
        orphan_count=1,
        rows_with_company_id=9,
        rows_without_company_id=1,
        linked_entity_role_counts={"canonical": 8, "missing_company": 0},
    )
    scorecard = evaluate_audit_payload(
        _payload(permits=mismatched, awards=_source(), schema_gap=False)
    )

    failures = scorecard["sources"]["permits"]["failures"]
    assert "MISSING_COMPANY_ROLE_COUNT_INCONSISTENT" in failures


def test_v2_payload_explicit_schema_version_is_echoed_back():
    versioned = _source(schema_version=2)
    scorecard = evaluate_audit_payload(
        _payload(permits=versioned, awards=_source(), schema_gap=False)
    )

    assert scorecard["sources"]["permits"]["schema_version"] == 2


# --- schema_version contract: declared version vs. actual shape ----------
#
# A declared schema_version is a claim about shape, and the evaluator must
# catch a false claim rather than silently reinterpreting it as whichever
# version the actual shape happens to look like.


def test_stamped_v2_without_breakdown_fails_with_dedicated_code():
    bad = _source_v1(schema_version=2)  # claims v2, omits the mandatory key
    scorecard = evaluate_audit_payload(
        _payload(permits=bad, awards=_source(), schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_MISSING" in permits["failures"]
    assert "ENTITY_ROLE_COUNT_INCONSISTENT" not in permits["failures"]
    assert "MISSING_COMPANY_ROLE_COUNT_INCONSISTENT" not in permits["failures"]
    assert permits["status"] == "FAIL"
    assert scorecard["overall_status"] == "FAIL"


def test_stamped_v1_with_breakdown_fails_with_dedicated_code():
    bad = _source(schema_version=1)  # claims v1, but carries the v2-only key
    scorecard = evaluate_audit_payload(
        _payload(permits=bad, awards=_source_v1(), schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED" in permits["failures"]
    assert "ENTITY_ROLE_COUNT_INCONSISTENT" not in permits["failures"]
    assert permits["status"] == "FAIL"
    assert scorecard["overall_status"] == "FAIL"


def test_correct_stamped_v1_evaluates_cleanly():
    clean = _source_v1(schema_version=1)
    scorecard = evaluate_audit_payload(
        _payload(permits=clean, awards=clean, schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_MISSING" not in permits["failures"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED" not in permits["failures"]
    assert permits["schema_version"] == 1
    assert permits["linked_entity_role_counts"] is None


def test_correct_stamped_v2_evaluates_cleanly_and_checks_role_counts():
    clean = _source(schema_version=2)  # default linked_entity_role_counts sums to 8
    scorecard = evaluate_audit_payload(
        _payload(permits=clean, awards=clean, schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_MISSING" not in permits["failures"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED" not in permits["failures"]
    assert permits["schema_version"] == 2
    assert permits["linked_entity_role_counts"] == {"canonical": 8}

    # A v2 payload is still fully checked — a genuine mismatch still fails,
    # proving the schema-version branch doesn't swallow the count check.
    broken = _source(schema_version=2, linked_entity_role_counts={"canonical": 3})
    broken_scorecard = evaluate_audit_payload(
        _payload(permits=broken, awards=clean, schema_gap=False)
    )
    assert (
        "ENTITY_ROLE_COUNT_INCONSISTENT"
        in broken_scorecard["sources"]["permits"]["failures"]
    )


def test_missing_version_old_v1_shape_infers_v1():
    old_v1 = _source_v1()
    scorecard = evaluate_audit_payload(
        _payload(permits=old_v1, awards=old_v1, schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert permits["schema_version"] == 1
    assert "SCHEMA_VERSION_ROLE_COUNTS_MISSING" not in permits["failures"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED" not in permits["failures"]


def test_missing_version_new_v2_shape_infers_v2():
    new_v2 = _source()  # has linked_entity_role_counts, no explicit schema_version
    scorecard = evaluate_audit_payload(
        _payload(permits=new_v2, awards=new_v2, schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert permits["schema_version"] == 2
    assert "SCHEMA_VERSION_ROLE_COUNTS_MISSING" not in permits["failures"]
    assert "SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED" not in permits["failures"]


def test_schema_version_true_is_rejected_as_not_an_integer():
    """bool is a subclass of int in Python — must be explicitly excluded."""
    bad = _source(schema_version=True)
    with pytest.raises(AuditEvaluationError, match="must be an integer"):
        evaluate_audit_payload(
            _payload(permits=bad, awards=_source(), schema_gap=False)
        )


def test_schema_version_string_is_rejected_as_not_an_integer():
    bad = _source(schema_version="2")
    with pytest.raises(AuditEvaluationError, match="must be an integer"):
        evaluate_audit_payload(
            _payload(permits=bad, awards=_source(), schema_gap=False)
        )


def test_schema_version_zero_is_rejected_as_unsupported():
    bad = _source(schema_version=0)
    with pytest.raises(AuditEvaluationError, match="unsupported"):
        evaluate_audit_payload(
            _payload(permits=bad, awards=_source(), schema_gap=False)
        )


def test_schema_version_above_current_is_rejected_as_unsupported():
    from pipeline.registry_engine.evidence.domain import CURRENT_SCHEMA_VERSION

    bad = _source(schema_version=CURRENT_SCHEMA_VERSION + 1)
    with pytest.raises(AuditEvaluationError, match="unsupported"):
        evaluate_audit_payload(
            _payload(permits=bad, awards=_source(), schema_gap=False)
        )


# --- absent key vs. present-but-null: not the same thing -----------------


def test_absent_breakdown_key_is_accepted_as_unstamped_v1():
    """The key genuinely missing from the payload (never written) is the
    only case that means "v1, no opinion" — distinct from the key being
    present with a null/wrong-shaped value, which is malformed."""
    missing_key = _source_v1()
    assert "linked_entity_role_counts" not in missing_key

    scorecard = evaluate_audit_payload(
        _payload(permits=missing_key, awards=missing_key, schema_gap=False)
    )

    permits = scorecard["sources"]["permits"]
    assert permits["schema_version"] == 1
    assert permits["linked_entity_role_counts"] is None
    assert permits["failures"] == []


def test_breakdown_key_present_as_null_is_rejected_as_malformed():
    """A present-but-null linked_entity_role_counts is not "no opinion" —
    it's a malformed v2-shaped field and must raise, not be silently
    reinterpreted as a v1 payload."""
    null_breakdown = _source(linked_entity_role_counts=None)
    with pytest.raises(AuditEvaluationError, match="must be an object"):
        evaluate_audit_payload(
            _payload(permits=null_breakdown, awards=_source(), schema_gap=False)
        )


def test_stamped_v2_with_null_breakdown_is_rejected_as_malformed():
    """Combining an explicit v2 stamp with a null breakdown must still raise
    at the shape-validation stage — not be caught later as a softer
    SCHEMA_VERSION_ROLE_COUNTS_MISSING scorecard failure."""
    bad = _source(schema_version=2, linked_entity_role_counts=None)
    with pytest.raises(AuditEvaluationError, match="must be an object"):
        evaluate_audit_payload(
            _payload(permits=bad, awards=_source(), schema_gap=False)
        )
