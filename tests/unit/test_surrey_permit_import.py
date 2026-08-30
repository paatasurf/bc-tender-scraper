"""Unit and local-Postgres tests for the Surrey identity-aware import
adapter (PR-EN1F-1, db.surrey_permit_import)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.models import Permit
from db.surrey_permit_import import (
    SurreyIdentityImportError,
    legacy_key,
    upsert_surrey_permit_identity_aware,
    upsert_surrey_permits_identity_aware,
)
from tests.db_schema_test_helpers import temporarily_drop_unique_index
from tests.db_test_safety import require_local_test_database

# --- pure-logic unit tests (no DB) -------------------------------------


def test_legacy_key_extracts_prefix_from_current_format():
    assert legacy_key("26-123456-001-00/AB") == "26-123456-001-00"


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
def test_legacy_key_rejects_unexpected_shapes(bad_id):
    assert legacy_key(bad_id) is None


def test_row_missing_external_id_fails_closed():
    class _SessionSpy:
        def __getattr__(self, name):
            raise AssertionError(f"session touched through {name}")

    with pytest.raises(SurreyIdentityImportError, match="missing external_id"):
        upsert_surrey_permit_identity_aware(_SessionSpy(), {"applicant": "X"})


# --- local-Postgres regression tests ------------------------------------


@pytest.fixture()
def db_session():
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
    session = Session(bind=conn)
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _legacy_id(index: int) -> str:
    unique = uuid.uuid4().int % 1_000_000
    return f"30-{unique:06d}-{index:03d}-00"


def test_tier1_updates_existing_row_by_official_source_id_without_touching_external_id(
    db_session,
):
    legacy_id = _legacy_id(1)
    official_number = f"{legacy_id}/AB"
    permit = Permit(
        address="Old Address",
        permit_type="Old Type",
        project_value="1",
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
    permit_id = int(permit.id)

    result = upsert_surrey_permit_identity_aware(
        db_session,
        {
            "external_id": official_number,
            "address": "New Address",
            "applicant": "New Applicant",
        },
    )
    assert result.outcome == "updated"
    assert result.permit_id == permit_id

    row = db_session.execute(
        text(
            "SELECT external_id, official_source_id, address, applicant, "
            "company_id, canonical_merge_confidence, canonical_merge_method "
            "FROM permits WHERE id = :id"
        ),
        {"id": permit_id},
    ).one()
    assert row.external_id == legacy_id  # never touched
    assert row.official_source_id == official_number
    assert row.address == "New Address"
    assert row.applicant == "New Applicant"
    assert row.company_id is None
    assert row.canonical_merge_confidence == 0.5
    assert row.canonical_merge_method == "existing_method"


def test_tier2_bridges_unbridged_legacy_row_by_prefix(db_session):
    legacy_id = _legacy_id(2)
    official_number = f"{legacy_id}/CD"
    permit = Permit(
        address="Legacy Address",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=None,
    )
    db_session.add(permit)
    db_session.flush()
    permit_id = int(permit.id)

    result = upsert_surrey_permit_identity_aware(
        db_session,
        {"external_id": official_number, "applicant": "Bridged Applicant"},
    )
    assert result.outcome == "updated"
    assert result.permit_id == permit_id

    row = db_session.execute(
        text(
            "SELECT external_id, official_source_id, applicant FROM permits "
            "WHERE id = :id"
        ),
        {"id": permit_id},
    ).one()
    assert row.external_id == legacy_id
    assert row.official_source_id == official_number
    assert row.applicant == "Bridged Applicant"


def test_tier3_inserts_new_self_identifying_row_and_reimport_finds_it_without_duplicate(
    db_session,
):
    unique = uuid.uuid4().int % 1_000_000
    official_number = f"31-{unique:06d}-001-00/EF"

    result = upsert_surrey_permit_identity_aware(
        db_session,
        {
            "external_id": official_number,
            "address": "Brand New Address",
            "applicant": "Brand New Applicant",
        },
    )
    assert result.outcome == "inserted"
    permit_id = result.permit_id

    row = db_session.execute(
        text("SELECT external_id, official_source_id FROM permits WHERE id = :id"),
        {"id": permit_id},
    ).one()
    assert row.external_id == official_number
    assert row.official_source_id == official_number

    # Re-import the exact same official number must find it via tier 1 --
    # never a second insert.
    second = upsert_surrey_permit_identity_aware(
        db_session,
        {"external_id": official_number, "address": "Updated Again"},
    )
    assert second.outcome == "updated"
    assert second.permit_id == permit_id

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' "
            "AND (external_id = :official OR official_source_id = :official)"
        ),
        {"official": official_number},
    ).scalar()
    assert count == 1


def test_blank_incoming_applicant_never_overwrites_existing_applicant(db_session):
    legacy_id = _legacy_id(4)
    official_number = f"{legacy_id}/GH"
    permit = Permit(
        address="Address",
        permit_type="",
        project_value="",
        applicant="Preserve Me Ltd.",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=official_number,
    )
    db_session.add(permit)
    db_session.flush()
    permit_id = int(permit.id)

    upsert_surrey_permit_identity_aware(
        db_session, {"external_id": official_number, "applicant": ""}
    )
    upsert_surrey_permit_identity_aware(
        db_session, {"external_id": official_number, "applicant": None}
    )

    row = db_session.execute(
        text("SELECT applicant FROM permits WHERE id = :id"), {"id": permit_id}
    ).one()
    assert row.applicant == "Preserve Me Ltd."


def test_ambiguous_legacy_match_fails_closed_and_batch_rolls_back(db_session):
    legacy_id = _legacy_id(5)
    official_number = f"{legacy_id}/IJ"
    first = Permit(
        address="A",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=None,
    )
    second = Permit(
        address="B",
        permit_type="",
        project_value="",
        applicant="",
        source="surrey",
        city="Surrey",
        external_id=legacy_id,
        official_source_id=None,
    )
    # ix_permits_source_external_id now forbids two permits sharing a
    # non-empty external_id at the DB level -- exactly the legacy-data
    # shape this fixture models predates that constraint. Dropping the
    # index for the rest of THIS test's own never-committed transaction
    # only (rolled back at fixture teardown, never visible to any other
    # session) lets the ambiguous-legacy-match code path this test exists
    # to exercise still run against real rows, without weakening the
    # schema for real. See tests/db_schema_test_helpers.py.
    with temporarily_drop_unique_index(db_session, "ix_permits_source_external_id"):
        db_session.add_all([first, second])
        db_session.flush()

    with pytest.raises(SurreyIdentityImportError, match="ambiguous legacy match"):
        upsert_surrey_permit_identity_aware(
            db_session, {"external_id": official_number}
        )


def test_batch_never_commits_itself_on_full_success(db_session):
    """upsert_surrey_permits_identity_aware must never call session.commit()
    on its own behalf -- only a future caller/runner may make a successful
    batch durable."""
    commits = []
    event.listen(db_session, "after_commit", lambda _session: commits.append(True))

    unique = uuid.uuid4().int % 1_000_000
    numbers = [f"34-{unique:06d}-{i:03d}-00/CC" for i in range(2)]
    rows = [
        {"external_id": number, "address": f"Row {index}"}
        for index, number in enumerate(numbers)
    ]

    result = upsert_surrey_permits_identity_aware(db_session, rows)
    assert result == {"updated": 0, "inserted": 2}
    assert commits == []


def test_batch_failure_leaves_no_row_from_the_batch_committed(db_session):
    """A batch with two valid rows followed by one failing row must, once
    the (future) caller rolls the whole transaction back, leave neither
    the update/insert of the earlier valid rows persisted -- the adapter
    itself never commits, so a caller-owned rollback is what makes the
    whole batch atomic."""
    unique = uuid.uuid4().int % 1_000_000
    good_number_1 = f"36-{unique:06d}-001-00/DD"
    good_number_2 = f"36-{unique:06d}-002-00/EE"
    rows = [
        {"external_id": good_number_1, "address": "Good Row 1"},
        {"external_id": good_number_2, "address": "Good Row 2"},
        {"external_id": "", "address": "Bad Row (missing external_id)"},
    ]

    with pytest.raises(SurreyIdentityImportError, match="missing external_id"):
        upsert_surrey_permits_identity_aware(db_session, rows)

    # Simulate the future Class-C runner's rollback-on-failure behavior.
    db_session.rollback()

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM permits WHERE source = 'surrey' "
            "AND external_id IN (:a, :b)"
        ),
        {"a": good_number_1, "b": good_number_2},
    ).scalar()
    assert count == 0
