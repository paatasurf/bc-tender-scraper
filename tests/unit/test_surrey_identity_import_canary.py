"""Tests for the aggregate-only Surrey identity-aware import canary plan,
the fixed, deterministic Class-C import canary writer core (PR-EN1F-3),
and the unbounded full-batch writer core (PR-EN1F-5)."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from pipeline.surrey_identity_import_canary import (
    FIXED_INSERT_LIMIT,
    FIXED_UPDATE_LIMIT,
    SurreyIdentityImportCanaryError,
    apply_surrey_identity_import_canary,
    apply_surrey_identity_import_full,
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


# --- apply_surrey_identity_import_canary: local-Postgres tests ---------


@pytest.fixture()
def db_session():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as RealSession

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
    conn.execute(text("SET LOCAL lock_timeout = '10s'"))
    session = RealSession(bind=conn)
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _run_marker() -> str:
    """A 4-digit marker, constant across every permit/row a single test
    creates, so that appending a 2-digit zero-padded index to it yields a
    legacy id whose lexicographic order matches the index order -- while
    still being unique enough across separate test invocations."""
    return f"{uuid.uuid4().int % 10_000:04d}"


def _make_update_permit(db_session, run_marker: str, index: int):
    from db.models import Permit

    legacy_id = f"38-{run_marker}{index:02d}-001-00"
    official_number = f"{legacy_id}/AB"
    permit = Permit(
        address="Old Address",
        permit_type="",
        project_value="",
        applicant="Old Applicant",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=official_number,
        company_id=None,
        canonical_merge_confidence=0.5,
        canonical_merge_method="existing_method",
    )
    db_session.add(permit)
    db_session.flush()
    return int(permit.id), official_number


def _make_insert_official_number(run_marker: str, index: int) -> str:
    return f"39-{run_marker}{index:02d}-001-00/CD"


def test_apply_canary_selects_fixed_20_5_deterministically_and_touches_only_selected(
    db_session,
):
    """25 update-eligible permits + 8 insert-eligible source rows exist,
    in creation (and, deliberately, official-PermitNumber-ascending)
    order; the canary must apply exactly the first 20 updates and first 5
    inserts by that canonical PermitNumber ordering, and leave the
    remaining 5 update-eligible rows and 3 insert-eligible rows
    completely untouched."""
    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(25)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(8)]
    assert update_permits == sorted(update_permits, key=lambda item: item[1])
    assert insert_numbers == sorted(insert_numbers)

    rows = [
        {"external_id": official, "applicant": "New Applicant"}
        for _permit_id, official in update_permits
    ] + [
        {"external_id": official, "applicant": "Brand New"}
        for official in insert_numbers
    ]

    report = plan_surrey_identity_import(db_session, rows=rows)
    assert report["counts"]["planned_updates"] == 25
    assert report["counts"]["planned_inserts"] == 8
    assert report["counts"]["duplicate_risk"] == 0

    result = apply_surrey_identity_import_canary(
        db_session,
        rows=rows,
        expected_plan_digest=report["plan_digest"],
    )
    assert result["eligible_updates"] == 25
    assert result["eligible_inserts"] == 8
    assert result["selected_updates"] == FIXED_UPDATE_LIMIT
    assert result["selected_inserts"] == FIXED_INSERT_LIMIT
    assert result["updated"] == FIXED_UPDATE_LIMIT
    assert result["inserted"] == FIXED_INSERT_LIMIT
    assert len(result["canary_digest"]) == 64
    assert result["canary_digest"] != result["plan_digest"]

    from sqlalchemy import text

    for permit_id, _official in update_permits[:20]:
        row = db_session.execute(
            text(
                "SELECT applicant, company_id, canonical_merge_confidence, "
                "canonical_merge_method FROM permits WHERE id = :id"
            ),
            {"id": permit_id},
        ).one()
        assert row.applicant == "New Applicant"
        assert row.company_id is None
        assert row.canonical_merge_confidence == 0.5
        assert row.canonical_merge_method == "existing_method"

    for permit_id, _official in update_permits[20:]:
        row = db_session.execute(
            text("SELECT applicant FROM permits WHERE id = :id"), {"id": permit_id}
        ).one()
        assert row.applicant == "Old Applicant"

    inserted_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' "
            "AND external_id = ANY(:numbers)"
        ),
        {"numbers": insert_numbers[:5]},
    ).scalar()
    assert inserted_count == 5

    not_inserted_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' "
            "AND external_id = ANY(:numbers)"
        ),
        {"numbers": insert_numbers[5:]},
    ).scalar()
    assert not_inserted_count == 0


def test_apply_canary_is_deterministic_across_repeated_dry_selection(db_session):
    """Selecting twice against the same unmodified plan (without applying)
    must yield the identical ordered candidate set."""
    from pipeline.surrey_identity_import_canary import _build_import_plan

    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(3)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(2)]
    rows = [{"external_id": official} for _permit_id, official in update_permits] + [
        {"external_id": official} for official in insert_numbers
    ]

    report_1, candidates_1 = _build_import_plan(db_session, rows=rows)
    report_2, candidates_2 = _build_import_plan(db_session, rows=rows)
    assert report_1["plan_digest"] == report_2["plan_digest"]
    assert [
        (c.permit_id, c.outcome, c.official_permit_number) for c in candidates_1
    ] == [(c.permit_id, c.outcome, c.official_permit_number) for c in candidates_2]


def test_apply_canary_selection_and_digest_are_input_order_independent(db_session):
    """The exact same set of source rows, given to the canary apply in two
    different (shuffled) input orders, must select the identical 20
    update + 5 insert candidates and produce the identical canary_digest
    -- selection is driven by the canonical PermitNumber sort key, never
    by the order the caller happened to supply rows in."""
    import random

    from sqlalchemy import text

    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(25)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(8)]

    rows_ascending = [
        {"external_id": official, "applicant": "New Applicant"}
        for _permit_id, official in update_permits
    ] + [
        {"external_id": official, "applicant": "Brand New"}
        for official in insert_numbers
    ]
    rows_shuffled = list(rows_ascending)
    random.Random(20260721).shuffle(rows_shuffled)
    assert [r["external_id"] for r in rows_shuffled] != [
        r["external_id"] for r in rows_ascending
    ]

    report_ascending = plan_surrey_identity_import(db_session, rows=rows_ascending)
    report_shuffled = plan_surrey_identity_import(db_session, rows=rows_shuffled)
    assert report_ascending["plan_digest"] == report_shuffled["plan_digest"]

    savepoint = db_session.begin_nested()
    result_ascending = apply_surrey_identity_import_canary(
        db_session,
        rows=rows_ascending,
        expected_plan_digest=report_ascending["plan_digest"],
    )
    applied_updates_ascending = {
        permit_id
        for permit_id, _official in update_permits[:20]
        if db_session.execute(
            text("SELECT applicant FROM permits WHERE id = :id"), {"id": permit_id}
        ).scalar()
        == "New Applicant"
    }
    savepoint.rollback()
    db_session.expire_all()

    savepoint = db_session.begin_nested()
    result_shuffled = apply_surrey_identity_import_canary(
        db_session,
        rows=rows_shuffled,
        expected_plan_digest=report_shuffled["plan_digest"],
    )
    applied_updates_shuffled = {
        permit_id
        for permit_id, _official in update_permits[:20]
        if db_session.execute(
            text("SELECT applicant FROM permits WHERE id = :id"), {"id": permit_id}
        ).scalar()
        == "New Applicant"
    }
    savepoint.rollback()
    db_session.expire_all()

    assert result_ascending["selected_updates"] == FIXED_UPDATE_LIMIT
    assert result_ascending["selected_inserts"] == FIXED_INSERT_LIMIT
    assert result_ascending["canary_digest"] == result_shuffled["canary_digest"]
    assert applied_updates_ascending == applied_updates_shuffled
    assert len(applied_updates_ascending) == FIXED_UPDATE_LIMIT
    # The canonical (lexicographic-by-PermitNumber) selection is exactly
    # the first 20/5 by construction order here, in both runs.
    assert applied_updates_ascending == {
        permit_id for permit_id, _official in update_permits[:20]
    }


def test_apply_canary_fails_closed_on_full_plan_digest_drift(db_session):
    """A digest computed before an extra ambiguity-causing row appears must
    be refused -- and refused before any row is written."""
    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(1)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(1)]
    rows = [{"external_id": official} for _permit_id, official in update_permits] + [
        {"external_id": official} for official in insert_numbers
    ]

    report = plan_surrey_identity_import(db_session, rows=rows)
    stale_digest = report["plan_digest"]

    # Drift the live universe: a second permit sharing the same
    # official_source_id makes the previously-single update ambiguous,
    # changing the full plan (and therefore the digest) without touching
    # `rows`.
    from db.models import Permit

    _permit_id, official_number = update_permits[0]
    confounder = Permit(
        address="",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=f"other-{uuid.uuid4().hex[:8]}",
        official_source_id=official_number,
    )
    db_session.add(confounder)
    db_session.flush()

    with pytest.raises(SurreyIdentityImportCanaryError, match="candidate set changed"):
        apply_surrey_identity_import_canary(
            db_session,
            rows=rows,
            expected_plan_digest=stale_digest,
        )

    from sqlalchemy import text

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' AND external_id = :n"
        ),
        {"n": insert_numbers[0]},
    ).scalar()
    assert count == 0


def test_apply_canary_applies_fewer_than_requested_without_raising_when_available(
    db_session,
):
    """The pipeline apply function itself does not enforce exact counts --
    that is the Class-C runner's job. Given fewer eligible rows than the
    requested limits, it applies whatever is available."""
    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(3)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(2)]
    rows = [{"external_id": official} for _permit_id, official in update_permits] + [
        {"external_id": official} for official in insert_numbers
    ]

    report = plan_surrey_identity_import(db_session, rows=rows)
    result = apply_surrey_identity_import_canary(
        db_session,
        rows=rows,
        expected_plan_digest=report["plan_digest"],
        update_limit=20,
        insert_limit=5,
    )
    assert result["updated"] == 3
    assert result["inserted"] == 2


def test_apply_canary_rejects_invalid_limits_and_digest(db_session):
    with pytest.raises(SurreyIdentityImportCanaryError):
        apply_surrey_identity_import_canary(
            db_session, rows=[], expected_plan_digest="a" * 64, update_limit=0
        )
    with pytest.raises(SurreyIdentityImportCanaryError):
        apply_surrey_identity_import_canary(
            db_session, rows=[], expected_plan_digest="a" * 64, insert_limit=0
        )
    with pytest.raises(SurreyIdentityImportCanaryError):
        apply_surrey_identity_import_canary(
            db_session, rows=[], expected_plan_digest="not-a-digest"
        )


# --- apply_surrey_identity_import_full: local-Postgres tests -----------


def test_apply_full_applies_every_update_and_insert_and_touches_only_selected(
    db_session,
):
    """15 update-eligible permits + 7 insert-eligible source rows exist;
    the full apply must update all 15 and insert all 7 -- not a bounded
    slice -- and change only allowed fields, never Company/company_id."""
    from sqlalchemy import text

    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(15)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(7)]

    rows = [
        {"external_id": official, "applicant": "New Applicant"}
        for _permit_id, official in update_permits
    ] + [
        {"external_id": official, "applicant": "Brand New"}
        for official in insert_numbers
    ]

    report = plan_surrey_identity_import(db_session, rows=rows)
    assert report["counts"]["planned_updates"] == 15
    assert report["counts"]["planned_inserts"] == 7
    assert report["counts"]["duplicate_risk"] == 0

    result = apply_surrey_identity_import_full(
        db_session,
        rows=rows,
        expected_plan_digest=report["plan_digest"],
    )
    assert result["eligible_updates"] == 15
    assert result["eligible_inserts"] == 7
    assert result["updated"] == 15
    assert result["inserted"] == 7
    assert result["plan_digest"] == report["plan_digest"]

    for permit_id, _official in update_permits:
        row = db_session.execute(
            text(
                "SELECT applicant, company_id, canonical_merge_confidence, "
                "canonical_merge_method FROM permits WHERE id = :id"
            ),
            {"id": permit_id},
        ).one()
        assert row.applicant == "New Applicant"
        assert row.company_id is None
        assert row.canonical_merge_confidence == 0.5
        assert row.canonical_merge_method == "existing_method"

    inserted_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' "
            "AND external_id = ANY(:numbers) AND company_id IS NULL"
        ),
        {"numbers": insert_numbers},
    ).scalar()
    assert inserted_count == 7


def test_apply_full_fails_closed_on_full_plan_digest_drift(db_session):
    """A digest computed before an extra ambiguity-causing row appears must
    be refused -- and refused before any row is written."""
    from sqlalchemy import text

    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(1)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(1)]
    rows = [{"external_id": official} for _permit_id, official in update_permits] + [
        {"external_id": official} for official in insert_numbers
    ]

    report = plan_surrey_identity_import(db_session, rows=rows)
    stale_digest = report["plan_digest"]

    from db.models import Permit

    _permit_id, official_number = update_permits[0]
    confounder = Permit(
        address="",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=f"other-{uuid.uuid4().hex[:8]}",
        official_source_id=official_number,
    )
    db_session.add(confounder)
    db_session.flush()

    with pytest.raises(SurreyIdentityImportCanaryError, match="candidate set changed"):
        apply_surrey_identity_import_full(
            db_session,
            rows=rows,
            expected_plan_digest=stale_digest,
        )

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' AND external_id = :n"
        ),
        {"n": insert_numbers[0]},
    ).scalar()
    assert count == 0


def test_apply_full_repeat_with_same_artifact_is_fail_closed(db_session):
    """After a successful full apply, re-running against the same
    (now-stale) rows and plan_digest must be refused -- applying the
    batch changes production, which changes the full plan (the newly
    inserted rows are now found via official_source_id on the next
    plan), so the digest can never match twice in a row."""
    marker = _run_marker()
    update_permits = [_make_update_permit(db_session, marker, i) for i in range(2)]
    insert_numbers = [_make_insert_official_number(marker, i) for i in range(2)]
    rows = [{"external_id": official} for _permit_id, official in update_permits] + [
        {"external_id": official} for official in insert_numbers
    ]

    report = plan_surrey_identity_import(db_session, rows=rows)
    result = apply_surrey_identity_import_full(
        db_session,
        rows=rows,
        expected_plan_digest=report["plan_digest"],
    )
    assert result["updated"] == 2
    assert result["inserted"] == 2

    with pytest.raises(SurreyIdentityImportCanaryError, match="candidate set changed"):
        apply_surrey_identity_import_full(
            db_session,
            rows=rows,
            expected_plan_digest=report["plan_digest"],
        )


def test_apply_full_rejects_invalid_digest(db_session):
    with pytest.raises(SurreyIdentityImportCanaryError):
        apply_surrey_identity_import_full(
            db_session, rows=[], expected_plan_digest="not-a-digest"
        )
