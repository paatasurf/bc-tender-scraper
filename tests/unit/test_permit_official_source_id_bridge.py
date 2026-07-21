"""Tests for the aggregate-only Surrey official-source-identity bridge plan."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from pipeline.permit_official_source_id_bridge import (
    PermitOfficialSourceIdBridgeError,
    apply_permit_official_source_id_bridge,
    compute_bridge_digest,
    plan_permit_official_source_id_bridge,
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


def _source(number: str):
    return {"PermitNumber": number}


def _permit(permit_id: int, external_id: str, official_source_id: str = ""):
    return SimpleNamespace(
        id=permit_id,
        external_id=external_id,
        official_source_id=official_source_id,
    )


def test_exact_match_is_counted_and_hashed():
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[_source("26-123456-001-00/AB")],
    )

    assert report["counts"]["overlapping_rows"] == 1
    assert report["candidate_count"] == 1
    assert len(report["candidate_set_digest"]) == 64


def test_existing_nonempty_official_source_id_is_never_a_candidate():
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(10, "26-123456-001-00", "26-123456-001-00/EXISTING")]),
        source_rows=[_source("26-123456-001-00/AB")],
    )

    assert report["counts"]["existing_nonempty_official_source_id"] == 1
    assert report["candidate_count"] == 0
    assert report["candidate_set_digest"] == hashlib.sha256(b"").hexdigest()


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
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[_source(bad_id)],
    )

    assert report["counts"]["invalid_source_ids"] == 1
    assert report["candidate_count"] == 0


def test_duplicate_full_source_id_is_counted():
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[
            _source("26-123456-001-00/AB"),
            _source("26-123456-001-00/AB"),
        ],
    )

    assert report["counts"]["duplicate_source_ids"] == 1


def test_duplicate_prefix_is_ambiguous_and_excluded():
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(10, "26-123456-001-00")]),
        source_rows=[
            _source("26-123456-001-00/AB"),
            _source("26-123456-001-00/X9"),
        ],
    )

    assert report["counts"]["ambiguous_legacy_prefixes"] == 1
    assert report["counts"]["duplicate_legacy_prefix_rows"] == 1
    assert report["counts"]["overlapping_rows"] == 0
    assert report["candidate_count"] == 0


def test_duplicate_production_key_is_ambiguous_and_excluded():
    report = plan_permit_official_source_id_bridge(
        _Session(
            [
                _permit(10, "26-123456-001-00"),
                _permit(11, "26-123456-001-00"),
            ]
        ),
        source_rows=[_source("26-123456-001-00/AB")],
    )

    assert report["counts"]["ambiguous_production_external_ids"] == 1
    assert report["counts"]["duplicate_production_legacy_ids"] == 1
    assert report["candidate_count"] == 0


def test_source_only_and_production_only_keys_are_reported():
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(10, "26-111111-001-00")]),
        source_rows=[_source("26-222222-001-00/AB")],
    )

    assert report["counts"]["source_only_keys"] == 1
    assert report["counts"]["production_only_keys"] == 1


def test_digest_is_order_independent_and_evidence_sensitive():
    first = compute_bridge_digest(
        [(2, "26-222222-001-00/A1"), (1, "26-111111-001-00/AB")]
    )
    reordered = compute_bridge_digest(
        [(1, "26-111111-001-00/AB"), (2, "26-222222-001-00/A1")]
    )
    changed = compute_bridge_digest(
        [(1, "26-111111-001-00/CHANGED"), (2, "26-222222-001-00/A1")]
    )
    assert first == reordered
    assert first != changed


@pytest.mark.parametrize("bad_id", [True, 0, -1, "1"])
def test_digest_rejects_invalid_database_ids(bad_id):
    with pytest.raises(PermitOfficialSourceIdBridgeError):
        compute_bridge_digest([(bad_id, "26-111111-001-00/AB")])


def test_report_never_serializes_raw_evidence_or_ids():
    secret_id = "26-123456-001-00/SECRET"
    report = plan_permit_official_source_id_bridge(
        _Session([_permit(987654321, "26-123456-001-00")]),
        source_rows=[_source(secret_id)],
    )
    serialized = json.dumps(report)
    assert secret_id not in serialized
    assert "987654321" not in serialized


def test_plan_uses_one_read_only_select_and_never_mutates_session():
    session = _Session([])
    plan_permit_official_source_id_bridge(session, source_rows=[])
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


def _digest_for(permit_id: int, source_permit_number: str) -> str:
    return compute_bridge_digest([(permit_id, source_permit_number)])


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

    with pytest.raises(PermitOfficialSourceIdBridgeError):
        apply_permit_official_source_id_bridge(
            _SessionSpy(),
            source_rows=[],
            candidate_limit=candidate_limit,
            expected_candidate_set_digest=digest,
        )


def test_writer_refuses_changed_candidate_set_before_any_update():
    session = _WriterSession([_permit(10, "26-123456-001-00")])
    with pytest.raises(
        PermitOfficialSourceIdBridgeError, match="candidate set changed"
    ):
        apply_permit_official_source_id_bridge(
            session,
            source_rows=[_source("26-123456-001-00/AB")],
            candidate_limit=1,
            expected_candidate_set_digest="0" * 64,
        )
    assert session.select_calls == 1
    assert session.update_statements == []


def test_writer_updates_only_official_source_id_for_digest_pinned_bounded_candidate():
    source_id = "26-123456-001-00/AB"
    session = _WriterSession(
        [
            _permit(10, "26-123456-001-00"),
            _permit(20, "26-222222-001-00"),
        ]
    )

    result = apply_permit_official_source_id_bridge(
        session,
        source_rows=[
            _source(source_id),
            _source("26-222222-001-00/A1"),
        ],
        candidate_limit=1,
        expected_candidate_set_digest=_digest_for(10, source_id),
    )

    assert result["eligible_count"] == 2
    assert result["selected_count"] == 1
    assert result["updated_count"] == 1
    assert len(session.update_statements) == 1
    statement = session.update_statements[0]
    sql = str(statement)
    set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert set_clause == "official_source_id=:official_source_id"
    assert "permits.id =" in sql
    assert "permits.source =" in sql
    assert "permits.external_id =" in sql
    assert "permits.official_source_id IS NULL OR permits.official_source_id =" in sql
    assert "applicant" not in sql
    assert "company_id" not in sql
    assert not hasattr(session, "commit")
    assert not hasattr(session, "flush")


def test_writer_fails_closed_when_blank_only_update_loses_race():
    source_id = "26-123456-001-00/AB"
    session = _WriterSession(
        [_permit(10, "26-123456-001-00")],
        update_rowcount=0,
    )
    with pytest.raises(PermitOfficialSourceIdBridgeError, match="exactly one row"):
        apply_permit_official_source_id_bridge(
            session,
            source_rows=[_source(source_id)],
            candidate_limit=1,
            expected_candidate_set_digest=_digest_for(10, source_id),
        )


def test_writer_never_serializes_raw_evidence_or_database_id():
    permit_id = 987654321
    source_id = "26-123456-001-00/AB"
    session = _WriterSession([_permit(permit_id, "26-123456-001-00")])
    result = apply_permit_official_source_id_bridge(
        session,
        source_rows=[_source(source_id)],
        candidate_limit=1,
        expected_candidate_set_digest=_digest_for(permit_id, source_id),
    )
    serialized = json.dumps(result)
    assert source_id not in serialized
    assert str(permit_id) not in serialized
