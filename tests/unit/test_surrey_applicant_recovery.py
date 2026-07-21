"""Tests for the aggregate-only Surrey applicant recovery plan."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from pipeline.surrey_applicant_recovery import (
    RECOMMENDED_CANARY_LIMIT,
    SurreyApplicantRecoveryError,
    apply_surrey_applicant_recovery,
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
    assert report["recommended_canary_limit"] == RECOMMENDED_CANARY_LIMIT
    assert report["canary_candidate_count"] == 1
    assert report["canary_candidate_set_digest"] == report["candidate_set_digest"]


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


class _WriterSession:
    def __init__(self, rows, *, update_rowcount=1):
        self.rows = rows
        self.update_rowcount = update_rowcount
        self.update_statements = []
        self.select_calls = 0

    def execute(self, statement):
        if getattr(statement, "is_select", False):
            self.select_calls += 1
            return _Result(self.rows)
        self.update_statements.append(statement)
        return SimpleNamespace(rowcount=self.update_rowcount)


def _digest_for(permit_id: int, source_id: str, applicant: str) -> str:
    return compute_recovery_digest([(permit_id, source_id, applicant)])


@pytest.mark.parametrize(
    ("candidate_limit", "digest"),
    [
        (0, "0" * 64),
        (-1, "0" * 64),
        (True, "0" * 64),
        ("1", "0" * 64),
        (1, ""),
        (1, "A" * 64),
        (1, "not-a-digest"),
    ],
)
def test_writer_rejects_invalid_contract_before_session_access(candidate_limit, digest):
    class _SessionSpy:
        def __getattr__(self, name):
            raise AssertionError(f"session touched through {name}")

    with pytest.raises(SurreyApplicantRecoveryError):
        apply_surrey_applicant_recovery(
            _SessionSpy(),
            source_rows=[],
            candidate_limit=candidate_limit,
            expected_candidate_set_digest=digest,
        )


def test_writer_refuses_changed_candidate_set_before_any_update():
    session = _WriterSession([_permit(10, "26-123456-001-00")])
    with pytest.raises(SurreyApplicantRecoveryError, match="candidate set changed"):
        apply_surrey_applicant_recovery(
            session,
            source_rows=[_source("26-123456-001-00/AB", "Builder Ltd.")],
            candidate_limit=1,
            expected_candidate_set_digest="0" * 64,
        )
    assert session.select_calls == 1
    assert session.update_statements == []


def test_writer_updates_only_applicant_for_digest_pinned_bounded_candidate():
    source_id = "26-123456-001-00/AB"
    applicant = "Builder Ltd."
    session = _WriterSession(
        [
            _permit(10, "26-123456-001-00"),
            _permit(20, "26-222222-001-00"),
        ]
    )

    result = apply_surrey_applicant_recovery(
        session,
        source_rows=[
            _source(source_id, applicant),
            _source("26-222222-001-00/A1", "Other Builder Inc."),
        ],
        candidate_limit=1,
        expected_candidate_set_digest=_digest_for(10, source_id, applicant),
    )

    assert result["eligible_count"] == 2
    assert result["selected_count"] == 1
    assert result["updated_count"] == 1
    assert len(session.update_statements) == 1
    statement = session.update_statements[0]
    sql = str(statement)
    set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert set_clause == "applicant=:applicant"
    assert "permits.id =" in sql
    assert "permits.source =" in sql
    assert "permits.external_id =" in sql
    assert "permits.applicant IS NULL OR permits.applicant =" in sql
    assert "company_id" not in sql
    assert not hasattr(session, "commit")
    assert not hasattr(session, "flush")


def test_writer_fails_closed_when_blank_only_update_loses_race():
    source_id = "26-123456-001-00/AB"
    applicant = "Builder Ltd."
    session = _WriterSession(
        [_permit(10, "26-123456-001-00")],
        update_rowcount=0,
    )
    with pytest.raises(SurreyApplicantRecoveryError, match="exactly one row"):
        apply_surrey_applicant_recovery(
            session,
            source_rows=[_source(source_id, applicant)],
            candidate_limit=1,
            expected_candidate_set_digest=_digest_for(10, source_id, applicant),
        )


def test_writer_never_serializes_raw_evidence_or_database_id():
    permit_id = 987654321
    source_id = "26-123456-001-00/AB"
    secret = "SECRET BUILDER NAME LTD."
    session = _WriterSession([_permit(permit_id, "26-123456-001-00")])
    result = apply_surrey_applicant_recovery(
        session,
        source_rows=[_source(source_id, secret)],
        candidate_limit=1,
        expected_candidate_set_digest=_digest_for(permit_id, source_id, secret),
    )
    serialized = json.dumps(result)
    assert source_id not in serialized
    assert secret not in serialized
    assert str(permit_id) not in serialized
