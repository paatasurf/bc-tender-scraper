"""Pure evaluation of Registry Engine Stage 2A audit JSON.

This module never opens a database connection. It converts the audit artifact
into a deterministic PASS/WARN/BLOCKED/FAIL scorecard using the acceptance
contract in reports/STAGE2A_EVIDENCE_AUDIT_ACCEPTANCE.md.

v1/v2 compatibility: v1 artifacts (produced before the
``linked_entity_role_counts`` breakdown existed) have no such key. This
evaluator treats that absence as "not applicable" rather than malformed —
it still runs every other check against a v1 artifact. Only a v2 artifact
(the key present) is checked against the two role-count invariants
(``ENTITY_ROLE_COUNT_INCONSISTENT``, ``MISSING_COMPANY_ROLE_COUNT_INCONSISTENT``).

An explicitly stamped ``schema_version`` is cross-checked against the
actual shape: a payload cannot claim v2 while omitting the breakdown
(``SCHEMA_VERSION_ROLE_COUNTS_MISSING``), or claim v1 while carrying one
(``SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED``) — both are schema
contradictions and always fail, never silently reinterpreted as the other
version. A malformed or out-of-range ``schema_version`` (non-integer, or
outside ``[SCHEMA_VERSION_V1, CURRENT_SCHEMA_VERSION]``) raises
``AuditEvaluationError`` rather than becoming a scorecard failure.
"""

from __future__ import annotations

import re
from typing import Any

from pipeline.registry_engine.evidence.domain import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
)

EVIDENCE_SOURCES = ("permits", "contract_awards")
STRUCTURAL_COUNTS = (
    "orphan_count",
    "broken_redirect_count",
    "cycle_count",
    "depth_exhausted_count",
    "excluded_target_count",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEVERITY = {"PASS": 0, "WARN": 1, "BLOCKED": 2, "FAIL": 3}


class AuditEvaluationError(ValueError):
    """Raised when an artifact does not satisfy the Stage 2A JSON contract."""


def _required_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise AuditEvaluationError(f"{key} must be an object")
    return value


def _required_non_negative_int(report: dict[str, Any], key: str, source: str) -> int:
    value = report.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AuditEvaluationError(f"{source}.{key} must be a non-negative integer")
    return value


def _highest_status(statuses: list[str]) -> str:
    return max(statuses, key=_SEVERITY.__getitem__)


def _evaluate_linked_entity_role_counts(
    report: dict[str, Any], source: str
) -> dict[str, int] | None:
    """Return the validated role-count breakdown, or ``None`` for a v1 payload.

    v1 Stage 2A artifacts (produced before this breakdown existed) simply
    don't have this key at all — that's not malformed input, it's an older
    schema version. But a key that IS present with a null/list/string/etc.
    value is not "absent," it's a malformed v2-shaped field — the key
    presence check must be `not in report`, not `.get(...) is None`, or a
    present-but-null field would be silently treated as "no opinion" (v1)
    instead of rejected.
    """
    if "linked_entity_role_counts" not in report:
        return None
    role_counts_raw = report["linked_entity_role_counts"]
    if not isinstance(role_counts_raw, dict):
        raise AuditEvaluationError(
            f"{source}.linked_entity_role_counts must be an object"
        )
    return {
        str(role): _required_non_negative_int(
            {"count": count}, "count", f"{source}.linked_entity_role_counts.{role}"
        )
        for role, count in role_counts_raw.items()
    }


def evaluate_evidence_source(source: str, report: dict[str, Any]) -> dict[str, Any]:
    total = _required_non_negative_int(report, "total_rows", source)
    linked = _required_non_negative_int(report, "rows_with_company_id", source)
    unlinked = _required_non_negative_int(report, "rows_without_company_id", source)
    non_canonical = _required_non_negative_int(report, "non_canonical_count", source)
    role_counts = _evaluate_linked_entity_role_counts(report, source)

    # schema_version contract:
    #   - absent -> inferred purely from whether linked_entity_role_counts is
    #     present (v2 if present, v1 if not). This keeps an un-stamped v2
    #     payload fully validated without requiring every producer to
    #     remember to stamp it.
    #   - present -> must be an int in [SCHEMA_VERSION_V1, CURRENT_SCHEMA_VERSION].
    #     Anything else (bool, str, out-of-range int) is malformed input, not
    #     a data-consistency finding, so it raises rather than becomes a
    #     scorecard failure.
    #   - present AND inconsistent with the actual shape (stamped v2 with no
    #     breakdown, or stamped v1 with one) is a schema contradiction, not a
    #     counting error — it gets its own dedicated failure code so it can
    #     never be masked as ENTITY_ROLE_COUNT_INCONSISTENT/MISSING_COMPANY_
    #     ROLE_COUNT_INCONSISTENT.
    declared_version = report.get("schema_version")
    if declared_version is not None:
        if isinstance(declared_version, bool) or not isinstance(declared_version, int):
            raise AuditEvaluationError(f"{source}.schema_version must be an integer")
        if not (SCHEMA_VERSION_V1 <= declared_version <= CURRENT_SCHEMA_VERSION):
            raise AuditEvaluationError(
                f"{source}.schema_version {declared_version} is unsupported "
                f"(must be between {SCHEMA_VERSION_V1} and {CURRENT_SCHEMA_VERSION})"
            )
        schema_version = declared_version
    else:
        schema_version = (
            SCHEMA_VERSION_V2 if role_counts is not None else SCHEMA_VERSION_V1
        )

    structural = {
        key: _required_non_negative_int(report, key, source)
        for key in STRUCTURAL_COUNTS
    }

    failures: list[str] = []
    warnings: list[str] = []
    if linked + unlinked != total:
        failures.append("ROW_COUNT_INCONSISTENT")
    if schema_version >= SCHEMA_VERSION_V2 and role_counts is None:
        failures.append("SCHEMA_VERSION_ROLE_COUNTS_MISSING")
    elif schema_version < SCHEMA_VERSION_V2 and role_counts is not None:
        failures.append("SCHEMA_VERSION_ROLE_COUNTS_UNEXPECTED")
    elif role_counts is not None:
        if sum(role_counts.values()) != linked:
            failures.append("ENTITY_ROLE_COUNT_INCONSISTENT")
        if role_counts.get("missing_company", 0) != structural["orphan_count"]:
            failures.append("MISSING_COMPANY_ROLE_COUNT_INCONSISTENT")
    for key, value in structural.items():
        if value:
            failures.append(key.upper())

    dataset_hash = report.get("dataset_hash")
    if not isinstance(dataset_hash, str) or not _SHA256_RE.fullmatch(dataset_hash):
        failures.append("INVALID_DATASET_HASH")

    if non_canonical:
        warnings.append("NON_CANONICAL_DIRECT_LINKS")
    if unlinked:
        warnings.append("UNLINKED_ROWS_PRESENT")

    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    linkage_rate = round(linked / total, 6) if total else None
    return {
        "source": source,
        "status": status,
        "schema_version": schema_version,
        "total_rows": total,
        "rows_with_company_id": linked,
        "rows_without_company_id": unlinked,
        "raw_linkage_rate": linkage_rate,
        "non_canonical_count": non_canonical,
        "linked_entity_role_counts": (
            dict(sorted(role_counts.items())) if role_counts is not None else None
        ),
        "structural_counts": structural,
        "dataset_hash": dataset_hash,
        "failures": failures,
        "warnings": warnings,
        "interpretation": (
            "Inventory baseline only; raw linkage rate is not precision or recall."
        ),
    }


def evaluate_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic scorecard for a Stage 2A audit payload."""
    if not isinstance(payload, dict):
        raise AuditEvaluationError("audit payload must be an object")

    source_results = {
        source: evaluate_evidence_source(source, _required_mapping(payload, source))
        for source in EVIDENCE_SOURCES
    }
    tender = _required_mapping(payload, "tenders")
    total_tenders = _required_non_negative_int(tender, "total_tenders", "tenders")
    has_company_id = tender.get("has_company_id_column")
    schema_gap = tender.get("schema_gap")
    if not isinstance(has_company_id, bool) or not isinstance(schema_gap, bool):
        raise AuditEvaluationError(
            "tenders.has_company_id_column and tenders.schema_gap must be booleans"
        )
    if schema_gap == has_company_id:
        raise AuditEvaluationError(
            "tenders.schema_gap must be the inverse of has_company_id_column"
        )

    tender_status = "BLOCKED" if schema_gap else "PASS"
    statuses = [result["status"] for result in source_results.values()]
    statuses.append(tender_status)
    overall = _highest_status(statuses)

    return {
        "contract_version": "stage2a_acceptance_v1",
        "overall_status": overall,
        "class": "Class A (No Write)",
        "sources": source_results,
        "tenders": {
            "status": tender_status,
            "total_tenders": total_tenders,
            "has_company_id_column": has_company_id,
            "schema_gap": schema_gap,
            "blockers": ["TENDER_COMPANY_ID_SCHEMA_GAP"] if schema_gap else [],
        },
        "customer_accuracy_claim_supported": False,
        "next_action": (
            "Review failures without writing data."
            if overall == "FAIL"
            else (
                "Approve a separate tender Evidence Link work package."
                if overall == "BLOCKED"
                else (
                    "Review warnings and define an adjudicated eligible denominator."
                    if overall == "WARN"
                    else "Define an adjudicated eligible denominator before accuracy claims."
                )
            )
        ),
    }
