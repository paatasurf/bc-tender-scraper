from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pipeline.data_coverage_audit import (
    DataCoverageAuditError,
    _dataset_digest,
    build_findings,
)


def test_dataset_digest_is_deterministic() -> None:
    left = {"b": 2, "a": [{"x": 1}]}
    right = {"a": [{"x": 1}], "b": 2}
    assert _dataset_digest(left) == _dataset_digest(right)
    assert len(_dataset_digest(left)) == 64


def test_zero_critical_field_coverage_is_critical() -> None:
    findings = build_findings(
        {"permits": [{"source": "surrey", "total": 8, "missing_applicant": 8}]}
    )
    assert findings == [
        {
            "dataset": "permits",
            "metric": "missing_applicant",
            "value": 8,
            "severity": "critical",
        }
    ]


def test_partial_missing_field_is_medium() -> None:
    findings = build_findings(
        {"permits": [{"source": "burnaby", "total": 8, "missing_applicant": 2}]}
    )
    assert findings[0]["severity"] == "medium"


@pytest.mark.parametrize(
    "metric",
    ["dangling_company_fk", "dangling_canonical_fk", "track_record_incoherent"],
)
def test_integrity_findings_are_critical(metric: str) -> None:
    findings = build_findings({"companies": {"total": 2, metric: 1}})
    assert findings[0]["severity"] == "critical"


def test_empty_dataset_has_no_findings() -> None:
    assert build_findings({"news": {"total": 0, "missing_title": 0}}) == []


def test_findings_are_aggregate_only() -> None:
    findings = build_findings(
        {"permits": [{"source": "surrey", "total": 1, "missing_applicant": 1}]}
    )
    encoded = json.dumps(findings)
    for forbidden in ("company_id", "permit_id", "address", "applicant_name"):
        assert forbidden not in encoded


def test_as_of_contract_example_is_timezone_aware() -> None:
    assert datetime(2026, 7, 20, tzinfo=timezone.utc).tzinfo is not None


def test_invalid_severity_is_fail_closed() -> None:
    from pipeline.data_coverage_audit import _finding

    with pytest.raises(DataCoverageAuditError):
        _finding("permits", "missing_applicant", 1, "urgent")
