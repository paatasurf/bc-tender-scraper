"""Pure evaluation of a Derived Tender Evidence Link Readiness Audit JSON.

This module never opens a database connection. It converts the audit
artifact produced by ``scripts/run_derived_tender_evidence_audit.py`` into a
deterministic PASS/WARN/BLOCKED/FAIL scorecard.

Independent contract: this does not import, extend, or modify
``pipeline.registry_engine.evidence.evaluate`` (the Stage 2A evaluator). The
two are evaluated separately, with separate failure/warning vocabularies.

Severity rule, as specified for this audit:

- dangling IDs or invalid internal invariants -> FAIL
- ambiguous external tender IDs that already have tender_outcomes evidence
  attached, or cross-path contradictions -> BLOCKED
- ambiguous external tender IDs with no evidence attached, missing
  coverage, shared awards, or non-awarded award links -> WARN
- otherwise -> PASS
"""

from __future__ import annotations

import re
from typing import Any

_SEVERITY = {"PASS": 0, "WARN": 1, "BLOCKED": 2, "FAIL": 3}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_SCHEMA_VERSION = 1


class DerivedTenderEvidenceEvaluationError(ValueError):
    """Raised when an artifact does not satisfy the expected JSON contract."""


def _required_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise DerivedTenderEvidenceEvaluationError(f"{key} must be an object")
    return value


def _required_non_negative_int(section: dict[str, Any], key: str, source: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DerivedTenderEvidenceEvaluationError(
            f"{source}.{key} must be a non-negative integer"
        )
    return value


def _required_count_dict(
    section: dict[str, Any], key: str, source: str
) -> dict[str, int]:
    value = section.get(key)
    if not isinstance(value, dict):
        raise DerivedTenderEvidenceEvaluationError(f"{source}.{key} must be an object")
    result: dict[str, int] = {}
    for bucket, count in value.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise DerivedTenderEvidenceEvaluationError(
                f"{source}.{key}.{bucket} must be a non-negative integer"
            )
        result[str(bucket)] = count
    return result


def _required_schema_version(section: dict[str, Any], source: str) -> int:
    """schema_version must be present and exactly the integer 1 — missing,
    bool, string, 0, and 2 are all rejected. A malformed or wrong version
    is a contract violation (this evaluator cannot interpret an artifact
    shaped differently than it expects), so it raises rather than becoming
    a scorecard failure — the same treatment as every other required-shape
    field in this module.
    """
    if "schema_version" not in section:
        raise DerivedTenderEvidenceEvaluationError(
            f"{source}.schema_version is required"
        )
    value = section["schema_version"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise DerivedTenderEvidenceEvaluationError(
            f"{source}.schema_version must be an integer"
        )
    if value != _REQUIRED_SCHEMA_VERSION:
        raise DerivedTenderEvidenceEvaluationError(
            f"{source}.schema_version must be {_REQUIRED_SCHEMA_VERSION} (got {value})"
        )
    return value


def _highest_status(statuses: list[str]) -> str:
    return max(statuses, key=_SEVERITY.__getitem__)


def evaluate_path_a(path_a: dict[str, Any]) -> dict[str, Any]:
    source = "path_a"
    _required_schema_version(path_a, source)
    inventory_total = _required_non_negative_int(path_a, "inventory_total", source)
    eligible_awarded_total = _required_non_negative_int(
        path_a, "eligible_awarded_total", source
    )
    awarded_with_award_id = _required_non_negative_int(
        path_a, "awarded_with_award_id", source
    )
    awarded_without_award_id = _required_non_negative_int(
        path_a, "awarded_without_award_id", source
    )
    non_awarded_with_award_id = _required_non_negative_int(
        path_a, "non_awarded_with_award_id", source
    )
    dangling_award_id_count = _required_non_negative_int(
        path_a, "dangling_award_id_count", source
    )
    award_without_company_count = _required_non_negative_int(
        path_a, "award_without_company_count", source
    )
    resolved_award_company_count = _required_non_negative_int(
        path_a, "resolved_award_company_count", source
    )
    resolved_awarded_winner_count = _required_non_negative_int(
        path_a, "resolved_awarded_winner_count", source
    )
    shared_award_id_count = _required_non_negative_int(
        path_a, "shared_award_id_count", source
    )
    shared_award_id_tender_count = _required_non_negative_int(
        path_a, "shared_award_id_tender_count", source
    )
    confidence_distribution = _required_count_dict(
        path_a, "match_confidence_distribution", source
    )
    entity_role_counts = _required_count_dict(path_a, "entity_role_counts", source)

    failures: list[str] = []
    warnings: list[str] = []

    if eligible_awarded_total != awarded_with_award_id + awarded_without_award_id:
        failures.append("PATH_A_AWARDED_TOTAL_INCONSISTENT")

    # The partition uses resolved_award_company_count (all-status) — every
    # tender with award_id set falls into exactly one of dangling/
    # award_without_company/resolved_award_company_count, regardless of
    # lifecycle_status. resolved_awarded_winner_count is a *subset* of
    # resolved_award_company_count (awarded-only) and must never appear in
    # this partition, or a non-awarded resolved link would silently vanish
    # from the invariant.
    total_with_award_id = awarded_with_award_id + non_awarded_with_award_id
    if (
        dangling_award_id_count
        + award_without_company_count
        + resolved_award_company_count
        != total_with_award_id
    ):
        failures.append("PATH_A_AWARD_ID_PARTITION_INCONSISTENT")

    if sum(confidence_distribution.values()) != total_with_award_id:
        failures.append("PATH_A_CONFIDENCE_DISTRIBUTION_INCONSISTENT")

    if resolved_awarded_winner_count > resolved_award_company_count:
        failures.append("PATH_A_WINNER_COUNT_EXCEEDS_RESOLVED_AWARDS")

    if resolved_awarded_winner_count > eligible_awarded_total:
        failures.append("PATH_A_WINNER_COUNT_EXCEEDS_ELIGIBLE")

    # entity_role_counts is scoped to awarded-only (see audit.py), so its
    # sum must equal resolved_awarded_winner_count exactly — not
    # resolved_award_company_count, which also includes non-awarded links.
    if sum(entity_role_counts.values()) != resolved_awarded_winner_count:
        failures.append("PATH_A_ENTITY_ROLE_COUNT_INCONSISTENT")

    dataset_hash = path_a.get("dataset_hash")
    if not isinstance(dataset_hash, str) or not _SHA256_RE.fullmatch(dataset_hash):
        failures.append("PATH_A_INVALID_DATASET_HASH")

    # A "shared" award_id is only shared with >= 2 tenders by definition —
    # the distinct-shared-id count and the tender-row count sharing them
    # must be zero together or non-zero together, and the tender count can
    # never be less than twice the distinct count.
    if (shared_award_id_count == 0) != (shared_award_id_tender_count == 0) or (
        shared_award_id_tender_count < 2 * shared_award_id_count
    ):
        failures.append("PATH_A_SHARED_AWARD_ID_COUNT_INCONSISTENT")

    if dangling_award_id_count:
        failures.append("DANGLING_AWARD_ID")

    if non_awarded_with_award_id:
        warnings.append("NON_AWARDED_WITH_AWARD_ID")
    if shared_award_id_count:
        warnings.append("SHARED_AWARD_IDS")
    if awarded_without_award_id:
        warnings.append("AWARDED_WITHOUT_AWARD_ID")
    if award_without_company_count:
        warnings.append("AWARD_WITHOUT_COMPANY")

    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    coverage_rate = (
        round(resolved_awarded_winner_count / eligible_awarded_total, 6)
        if eligible_awarded_total
        else None
    )

    return {
        "status": status,
        "inventory_total": inventory_total,
        "eligible_awarded_total": eligible_awarded_total,
        "resolved_award_company_count": resolved_award_company_count,
        "resolved_awarded_winner_count": resolved_awarded_winner_count,
        "coverage_rate": coverage_rate,
        "entity_role_counts": entity_role_counts,
        "dataset_hash": dataset_hash,
        "failures": failures,
        "warnings": warnings,
    }


def evaluate_path_b(path_b: dict[str, Any]) -> dict[str, Any]:
    source = "path_b"
    _required_schema_version(path_b, source)
    inventory_total = _required_non_negative_int(path_b, "inventory_total", source)
    tenders_with_valid_external_id = _required_non_negative_int(
        path_b, "tenders_with_valid_external_id", source
    )
    tenders_missing_external_id = _required_non_negative_int(
        path_b, "tenders_missing_external_id", source
    )
    ambiguous_external_id_tender_count = _required_non_negative_int(
        path_b, "ambiguous_external_id_tender_count", source
    )
    ambiguous_external_id_distinct_count = _required_non_negative_int(
        path_b, "ambiguous_external_id_distinct_count", source
    )
    ambiguous_external_id_with_outcomes_distinct_count = _required_non_negative_int(
        path_b, "ambiguous_external_id_with_outcomes_distinct_count", source
    )
    ambiguous_outcome_row_count = _required_non_negative_int(
        path_b, "ambiguous_outcome_row_count", source
    )
    safely_attributable_tenders = _required_non_negative_int(
        path_b, "safely_attributable_tenders", source
    )
    tenders_with_reported_bidders = _required_non_negative_int(
        path_b, "tenders_with_reported_bidders", source
    )
    dangling_company_id_count = _required_non_negative_int(
        path_b, "dangling_company_id_count", source
    )
    bidder_count_distribution = _required_count_dict(
        path_b, "bidder_count_distribution", source
    )
    outcomes_breakdown = _required_count_dict(path_b, "outcomes_breakdown", source)
    entity_role_counts = _required_count_dict(path_b, "entity_role_counts", source)

    failures: list[str] = []
    warnings: list[str] = []
    blocked: list[str] = []

    if inventory_total != tenders_with_valid_external_id + tenders_missing_external_id:
        failures.append("PATH_B_EXTERNAL_ID_TOTAL_INCONSISTENT")

    if (
        safely_attributable_tenders
        != tenders_with_valid_external_id - ambiguous_external_id_tender_count
    ):
        failures.append("PATH_B_ATTRIBUTABLE_TOTAL_INCONSISTENT")

    if tenders_with_reported_bidders != sum(bidder_count_distribution.values()):
        failures.append("PATH_B_BIDDER_DISTRIBUTION_INCONSISTENT")

    if tenders_with_reported_bidders > safely_attributable_tenders:
        failures.append("PATH_B_BIDDER_COUNT_EXCEEDS_ATTRIBUTABLE")

    # Every attributable outcome row is either resolved (counted once, by
    # its company's entity_role) or dangling (counted in
    # dangling_company_id_count) — never both, never neither — so the two
    # must sum to exactly the total attributable row count reflected in
    # outcomes_breakdown.
    if sum(entity_role_counts.values()) + dangling_company_id_count != sum(
        outcomes_breakdown.values()
    ):
        failures.append("PATH_B_ENTITY_ROLE_COUNT_INCONSISTENT")

    dataset_hash = path_b.get("dataset_hash")
    if not isinstance(dataset_hash, str) or not _SHA256_RE.fullmatch(dataset_hash):
        failures.append("PATH_B_INVALID_DATASET_HASH")

    # An ambiguous tender_id is only ambiguous when shared by >= 2 tender
    # rows by definition — same zero-together/non-zero-together and >= 2x
    # relationship as the Path A shared-award-id check.
    if (ambiguous_external_id_distinct_count == 0) != (
        ambiguous_external_id_tender_count == 0
    ) or (
        ambiguous_external_id_tender_count < 2 * ambiguous_external_id_distinct_count
    ):
        failures.append("PATH_B_AMBIGUOUS_EXTERNAL_ID_COUNT_INCONSISTENT")

    # The with-outcomes subset must be internally consistent too: zero
    # distinct with-outcomes IDs iff zero outcome rows, at least one row
    # per with-outcomes ID, and never more with-outcomes IDs than
    # ambiguous IDs in total (it is a subset of them).
    if (
        (ambiguous_external_id_with_outcomes_distinct_count == 0)
        != (ambiguous_outcome_row_count == 0)
        or (
            ambiguous_outcome_row_count
            < ambiguous_external_id_with_outcomes_distinct_count
        )
        or (
            ambiguous_external_id_with_outcomes_distinct_count
            > ambiguous_external_id_distinct_count
        )
    ):
        failures.append("PATH_B_AMBIGUOUS_WITH_OUTCOMES_COUNT_INCONSISTENT")

    if dangling_company_id_count:
        failures.append("DANGLING_COMPANY_ID_PATH_B")

    # A duplicate tender_id that already has tender_outcomes evidence
    # attached actively blocks safe attribution of that evidence; a
    # duplicate with no evidence yet is a data-quality nuisance, not a
    # blocker.
    if ambiguous_external_id_with_outcomes_distinct_count:
        blocked.append("AMBIGUOUS_EXTERNAL_TENDER_ID_WITH_EVIDENCE")
    elif ambiguous_external_id_distinct_count:
        warnings.append("AMBIGUOUS_EXTERNAL_TENDER_IDS_PRESENT")

    if tenders_missing_external_id:
        warnings.append("MISSING_EXTERNAL_TENDER_ID")

    status = (
        "FAIL" if failures else "BLOCKED" if blocked else "WARN" if warnings else "PASS"
    )

    return {
        "status": status,
        "inventory_total": inventory_total,
        "safely_attributable_tenders": safely_attributable_tenders,
        "tenders_with_reported_bidders": tenders_with_reported_bidders,
        "ambiguous_external_id_distinct_count": ambiguous_external_id_distinct_count,
        "ambiguous_external_id_with_outcomes_distinct_count": (
            ambiguous_external_id_with_outcomes_distinct_count
        ),
        "ambiguous_outcome_row_count": ambiguous_outcome_row_count,
        "entity_role_counts": entity_role_counts,
        "outcomes_breakdown": outcomes_breakdown,
        "dataset_hash": dataset_hash,
        "failures": failures,
        "blocked": blocked,
        "warnings": warnings,
    }


def evaluate_cross_path(cross_path: dict[str, Any]) -> dict[str, Any]:
    source = "cross_path"
    _required_schema_version(cross_path, source)
    comparable_tender_count = _required_non_negative_int(
        cross_path, "comparable_tender_count", source
    )
    ambiguous_excluded_count = _required_non_negative_int(
        cross_path, "ambiguous_excluded_count", source
    )
    different_winner = _required_non_negative_int(
        cross_path, "different_winner", source
    )
    winner_marked_lost = _required_non_negative_int(
        cross_path, "winner_marked_lost", source
    )
    winner_marked_withdrawn = _required_non_negative_int(
        cross_path, "winner_marked_withdrawn", source
    )
    winner_marked_pending = _required_non_negative_int(
        cross_path, "winner_marked_pending", source
    )
    same_winner_confirmed_won = _required_non_negative_int(
        cross_path, "same_winner_confirmed_won", source
    )

    # winner_marked_pending is reported below but deliberately does not
    # block: a pending self-reported outcome is simply not yet resolved,
    # not a contradiction of Path A's confirmed winner. lost/withdrew are
    # genuine contradictions (the self-report disagrees with the
    # confirmed award) and different_winner is a genuine conflicting claim
    # — those three block; pending does not.
    blocked: list[str] = []
    if different_winner:
        blocked.append("CROSS_PATH_DIFFERENT_WINNER")
    if winner_marked_lost:
        blocked.append("CROSS_PATH_WINNER_MARKED_LOST")
    if winner_marked_withdrawn:
        blocked.append("CROSS_PATH_WINNER_MARKED_WITHDRAWN")

    status = "BLOCKED" if blocked else "PASS"

    return {
        "status": status,
        "comparable_tender_count": comparable_tender_count,
        "ambiguous_excluded_count": ambiguous_excluded_count,
        "same_winner_confirmed_won": same_winner_confirmed_won,
        "winner_marked_pending": winner_marked_pending,
        "blocked": blocked,
    }


def evaluate_derived_tender_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic scorecard for a Derived Tender Evidence audit payload."""
    if not isinstance(payload, dict):
        raise DerivedTenderEvidenceEvaluationError("audit payload must be an object")

    _required_schema_version(payload, "payload")

    path_a_result = evaluate_path_a(_required_mapping(payload, "path_a"))
    path_b_result = evaluate_path_b(_required_mapping(payload, "path_b"))
    cross_path_result = evaluate_cross_path(_required_mapping(payload, "cross_path"))

    overall = _highest_status(
        [path_a_result["status"], path_b_result["status"], cross_path_result["status"]]
    )

    return {
        "contract_version": "derived_tender_evidence_v1",
        "overall_status": overall,
        "class": "Class A (No Write)",
        "path_a": path_a_result,
        "path_b": path_b_result,
        "cross_path": cross_path_result,
        "tenders_company_id_created": False,
        "next_action": (
            "Investigate dangling IDs and internal invariant failures before anything else."
            if overall == "FAIL"
            else (
                "Resolve ambiguous external tender IDs and cross-path contradictions "
                "before treating either path as authoritative."
                if overall == "BLOCKED"
                else (
                    "Review coverage gaps; no write action is implied by this audit."
                    if overall == "WARN"
                    else "Both paths are internally consistent and agree where comparable."
                )
            )
        ),
    }
