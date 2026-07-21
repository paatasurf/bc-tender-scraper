"""Tests for the aggregate-only Surrey applicant recovery plan."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from pipeline.surrey_applicant_recovery import (
    SurreyApplicantRecoveryError,
    compute_recovery_digest,
    plan_surrey_applicant_recovery,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = 0

    def execute(self, _query):
        self.execute_calls += 1
        return _Result(self.rows)


def _source(number: str, applicant: str | None):
    return {
        "PermitNumber": number,
        "ApplicantOrganization": applicant,
    }


def _permit(permit_id: int, external_id: str, applicant: str = ""):
    return SimpleNamespace(
        id=permit_id,
        external_id=external_id,
        applicant=applicant,
    )


def test_recoverable_blank_applicant_is_counted_and_hashed():
    report = plan_surrey_applicant_recovery(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[_source("26-123456-001-00/AB", "Builder Ltd.")],
    )

    assert report["counts"]["overlapping_rows"] == 1
    assert report["counts"]["recoverable_blank_applicant"] == 1
    assert report["candidate_count"] == 1
    assert len(report["candidate_set_digest"]) == 64


def test_existing_applicant_is_never_a_candidate():
    report = plan_surrey_applicant_recovery(
        _Session([_permit(10, "26-123456-001-00", "Existing Evidence")]),
        source_rows=[_source("26-123456-001-00/A1", "New Evidence")],
    )

    assert report["counts"]["already_populated_applicant"] == 1
    assert report["counts"]["recoverable_blank_applicant"] == 0
    assert report["candidate_set_digest"] == hashlib.sha256(b"").hexdigest()


def test_missing_source_applicant_never_blanks_or_becomes_candidate():
    report = plan_surrey_applicant_recovery(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[_source("26-123456-001-00/A1BC", None)],
    )

    assert report["counts"]["source_missing_applicant"] == 1
    assert report["counts"]["overlap_source_missing_applicant"] == 1
    assert report["candidate_count"] == 0


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "26-123456-001-00",
        "not-a-permit",
        "26-123456-001-00ABC",
        "26-123456-001-00/ABC",
        "26-123456-001-00/A123",
    ],
)
def test_unexpected_source_id_shapes_fail_closed_as_invalid(bad_id):
    report = plan_surrey_applicant_recovery(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[_source(bad_id, "Builder Ltd.")],
    )

    assert report["counts"]["invalid_source_ids"] == 1
    assert report["candidate_count"] == 0


def test_duplicate_prefix_is_ambiguous_and_excluded():
    report = plan_surrey_applicant_recovery(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[
            _source("26-123456-001-00/AB", "Builder One Ltd."),
            _source("26-123456-001-00/X9", "Builder Two Ltd."),
        ],
    )

    assert report["counts"]["ambiguous_legacy_prefixes"] == 1
    assert report["counts"]["duplicate_legacy_prefix_rows"] == 1
    assert report["counts"]["overlapping_rows"] == 0
    assert report["candidate_count"] == 0


def test_duplicate_production_key_is_ambiguous_and_excluded():
    report = plan_surrey_applicant_recovery(
        _Session(
            [
                _permit(10, "26-123456-001-00"),
                _permit(11, "26-123456-001-00"),
            ]
        ),
        source_rows=[_source("26-123456-001-00/A1BC", "Builder Ltd.")],
    )

    assert report["counts"]["ambiguous_production_external_ids"] == 1
    assert report["candidate_count"] == 0


def test_source_only_and_production_only_keys_are_reported():
    report = plan_surrey_applicant_recovery(
        _Session([_permit(10, "26-111111-001-00")]),
        source_rows=[_source("26-222222-001-00/AB", "Builder Ltd.")],
    )

    assert report["counts"]["source_only_keys"] == 1
    assert report["counts"]["production_only_keys"] == 1


def test_candidate_digest_is_order_independent_and_evidence_sensitive():
    first = compute_recovery_digest(
        [(2, "26-222222-001-00/A1", "B Ltd."), (1, "26-111111-001-00/AB", "A Ltd.")]
    )
    reordered = compute_recovery_digest(
        [(1, "26-111111-001-00/AB", "A Ltd."), (2, "26-222222-001-00/A1", "B Ltd.")]
    )
    changed = compute_recovery_digest(
        [
            (1, "26-111111-001-00/AB", "Changed Ltd."),
            (2, "26-222222-001-00/A1", "B Ltd."),
        ]
    )
    assert first == reordered
    assert first != changed


@pytest.mark.parametrize("bad_id", [True, 0, -1, "1"])
def test_candidate_digest_rejects_invalid_database_ids(bad_id):
    with pytest.raises(SurreyApplicantRecoveryError):
        compute_recovery_digest([(bad_id, "26-111111-001-00/AB", "Builder Ltd.")])


def test_report_never_serializes_raw_evidence_or_ids():
    secret_id = "26-123456-001-00/SECRET"
    secret_applicant = "HIGHLY SECRET BUILDER LTD"
    report = plan_surrey_applicant_recovery(
        _Session([_permit(987654321, "26-123456-001-00")]),
        source_rows=[_source(secret_id, secret_applicant)],
    )
    serialized = json.dumps(report)
    assert secret_id not in serialized
    assert secret_applicant not in serialized
    assert "987654321" not in serialized


def test_plan_uses_one_read_only_select_and_never_mutates_session():
    session = _Session([])
    plan_surrey_applicant_recovery(session, source_rows=[])
    assert session.execute_calls == 1
    assert not hasattr(session, "add")
    assert not hasattr(session, "commit")
