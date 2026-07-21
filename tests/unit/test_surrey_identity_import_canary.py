"""Tests for the aggregate-only Surrey identity-aware import canary plan."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline.surrey_identity_import_canary import (
    SurreyIdentityImportCanaryError,
    compute_plan_digest,
    plan_surrey_identity_import,
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


def _source(number: str, applicant: str | None = None):
    return {"external_id": number, "applicant": applicant}


def _permit(
    permit_id: int, external_id: str, official_source_id: str = "", applicant: str = ""
):
    return SimpleNamespace(
        id=permit_id,
        external_id=external_id,
        official_source_id=official_source_id,
        applicant=applicant,
    )


def test_planned_update_via_official_source_id_match():
    report = plan_surrey_identity_import(
        _Session(
            [_permit(10, "26-123456-001-00", official_source_id="26-123456-001-00/AB")]
        ),
        rows=[_source("26-123456-001-00/AB")],
    )
    assert report["counts"]["planned_updates"] == 1
    assert report["counts"]["planned_inserts"] == 0
    assert report["counts"]["duplicate_risk"] == 0
    assert len(report["plan_digest"]) == 64


def test_planned_update_via_unbridged_legacy_prefix():
    report = plan_surrey_identity_import(
        _Session([_permit(10, "26-123456-001-00")]),
        rows=[_source("26-123456-001-00/AB")],
    )
    assert report["counts"]["planned_updates"] == 1
    assert report["counts"]["planned_inserts"] == 0


def test_planned_insert_when_no_match():
    report = plan_surrey_identity_import(
        _Session([]),
        rows=[_source("26-123456-001-00/AB")],
    )
    assert report["counts"]["planned_inserts"] == 1
    assert report["counts"]["planned_updates"] == 0


def test_blank_applicant_preservation_is_counted():
    report = plan_surrey_identity_import(
        _Session(
            [
                _permit(
                    10,
                    "26-123456-001-00",
                    official_source_id="26-123456-001-00/AB",
                    applicant="Existing",
                )
            ]
        ),
        rows=[_source("26-123456-001-00/AB", applicant=None)],
    )
    assert report["counts"]["blank_applicant_preserved"] == 1


def test_nonblank_incoming_applicant_is_not_counted_as_preserved():
    report = plan_surrey_identity_import(
        _Session(
            [
                _permit(
                    10,
                    "26-123456-001-00",
                    official_source_id="26-123456-001-00/AB",
                    applicant="Existing",
                )
            ]
        ),
        rows=[_source("26-123456-001-00/AB", applicant="New Applicant")],
    )
    assert report["counts"]["blank_applicant_preserved"] == 0


def test_ambiguous_official_match_is_duplicate_risk():
    report = plan_surrey_identity_import(
        _Session(
            [
                _permit(
                    10, "26-000001-001-00", official_source_id="26-123456-001-00/AB"
                ),
                _permit(
                    11, "26-000002-001-00", official_source_id="26-123456-001-00/AB"
                ),
            ]
        ),
        rows=[_source("26-123456-001-00/AB")],
    )
    assert report["counts"]["duplicate_risk"] == 1
    assert report["counts"]["planned_updates"] == 0


def test_ambiguous_unbridged_legacy_match_is_duplicate_risk():
    report = plan_surrey_identity_import(
        _Session(
            [
                _permit(10, "26-123456-001-00"),
                _permit(11, "26-123456-001-00"),
            ]
        ),
        rows=[_source("26-123456-001-00/AB")],
    )
    assert report["counts"]["duplicate_risk"] == 1
    assert report["counts"]["planned_updates"] == 0


def test_invalid_and_duplicate_source_rows_are_counted():
    report = plan_surrey_identity_import(
        _Session([]),
        rows=[
            _source(""),
            _source("26-123456-001-00/AB"),
            _source("26-123456-001-00/AB"),
        ],
    )
    assert report["counts"]["invalid_rows"] == 1
    assert report["counts"]["duplicate_source_rows"] == 1
    assert report["counts"]["planned_inserts"] == 1


def test_digest_is_order_independent_and_evidence_sensitive():
    first = compute_plan_digest([(2, "insert", "26-2"), (1, "update", "26-1")])
    reordered = compute_plan_digest([(1, "update", "26-1"), (2, "insert", "26-2")])
    changed = compute_plan_digest(
        [(1, "update", "26-1-changed"), (2, "insert", "26-2")]
    )
    assert first == reordered
    assert first != changed


@pytest.mark.parametrize(
    ("permit_id", "official"), [(-1, "26-1"), (0.5, "26-1"), (0, "")]
)
def test_digest_rejects_invalid_entries(permit_id, official):
    with pytest.raises(SurreyIdentityImportCanaryError):
        compute_plan_digest([(permit_id, "update", official)])


def test_report_never_serializes_raw_evidence_or_ids():
    secret_number = "26-123456-001-00/SECRET"
    secret_applicant = "HIGHLY SECRET BUILDER LTD"
    report = plan_surrey_identity_import(
        _Session([_permit(987654321, "26-123456-001-00")]),
        rows=[_source(secret_number, applicant=secret_applicant)],
    )
    serialized = json.dumps(report)
    assert secret_number not in serialized
    assert secret_applicant not in serialized
    assert "987654321" not in serialized


def test_plan_uses_one_read_only_select_and_never_mutates_session():
    session = _Session([])
    plan_surrey_identity_import(session, rows=[])
    assert session.execute_calls == 1
    assert not hasattr(session, "add")
    assert not hasattr(session, "commit")


def test_plan_against_real_postgres_causes_zero_mutation():
    """End-to-end proof: running the planner against a real session, then
    rolling that transaction back, leaves the table exactly as it was --
    the planner itself commits nothing and writes nothing."""
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as RealSession

    from db.models import Permit
    from tests.db_test_safety import require_local_test_database

    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    conn = engine.connect()
    outer = conn.begin()
    try:
        session = RealSession(bind=conn)
        unique = uuid.uuid4().int % 1_000_000
        legacy_id = f"33-{unique:06d}-001-00"
        permit = Permit(
            address="Untouched Address",
            permit_type="",
            project_value="",
            applicant="Untouched Applicant",
            source="surrey",
            city="Surrey",
            external_id=legacy_id,
            official_source_id=None,
        )
        session.add(permit)
        session.flush()
        permit_id = int(permit.id)

        before = session.execute(
            text(
                "SELECT address, applicant, external_id, official_source_id "
                "FROM permits WHERE id = :id"
            ),
            {"id": permit_id},
        ).one()

        plan_surrey_identity_import(
            session, rows=[{"external_id": f"{legacy_id}/AB", "applicant": "New"}]
        )

        after = session.execute(
            text(
                "SELECT address, applicant, external_id, official_source_id "
                "FROM permits WHERE id = :id"
            ),
            {"id": permit_id},
        ).one()
        assert before == after
        session.close()
    finally:
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()
