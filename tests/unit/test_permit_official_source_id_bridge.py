"""Tests for the aggregate-only Surrey official-source-identity bridge plan."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from pipeline.permit_official_source_id_bridge import (
    PermitOfficialSourceIdBridgeError,
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
