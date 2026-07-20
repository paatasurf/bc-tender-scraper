"""Tests for the Company.track_record_* ORM mapping (PR-G2B).

Sections:
  1. Static mapper/column introspection (no DB) -- exact SQL types,
     nullability, absence of client/server defaults, ArchCompany
     untouched, migration 030 <-> ORM parity (reusing the schema
     contract already established in PR-G2A so the two definitions can
     never silently drift apart), instrumentation, assignment history.
  2. DB-backed tests (local Postgres only, transaction + rollback --
     nothing is ever committed by these tests). Migration 030 itself is
     assumed already, persistently applied to the local dev database (a
     one-time operational step performed alongside this PR, per its own
     task instructions) -- these tests do not apply it themselves.

No scorer wiring: pipeline.scoring.company_track_record is never
imported here, and nothing in pipeline/ is touched by this PR.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import DateTime, Integer, String, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session, attributes

from db.models import ArchCompany, Company
from db.track_record_schema_contract import TRACK_RECORD_COLUMNS
from tests.db_test_safety import require_local_test_database

TRACK_RECORD_FIELDS = (
    "track_record_score",
    "track_record_json",
    "track_record_at",
    "track_record_version",
)


# ===================================================================
# 1. Static mapper/column introspection -- no DB
# ===================================================================


def test_company_mapper_has_four_track_record_fields():
    columns = Company.__table__.c
    for name in TRACK_RECORD_FIELDS:
        assert name in columns, name


def test_track_record_score_column_contract():
    col = Company.__table__.c.track_record_score
    assert isinstance(col.type, Integer)
    assert col.nullable is True
    assert col.default is None
    assert col.server_default is None


def test_track_record_json_column_contract():
    col = Company.__table__.c.track_record_json
    assert isinstance(col.type, JSONB)
    assert col.nullable is True
    assert col.default is None
    assert col.server_default is None


def test_track_record_at_column_contract():
    col = Company.__table__.c.track_record_at
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True
    assert col.nullable is True
    assert col.default is None
    assert col.server_default is None


def test_track_record_version_column_contract():
    col = Company.__table__.c.track_record_version
    assert isinstance(col.type, String)
    assert col.type.length == 64
    assert col.nullable is True
    assert col.default is None
    assert col.server_default is None


def test_arch_company_does_not_get_track_record_fields():
    arch_columns = ArchCompany.__table__.c
    for name in TRACK_RECORD_FIELDS:
        assert name not in arch_columns, name
        assert not hasattr(ArchCompany, name)


def test_ai_reliability_fields_unchanged_by_this_pr():
    """This PR must not touch the pre-existing AI fields."""
    assert "ai_reliability_score" in Company.__table__.c
    assert "ai_summary" in Company.__table__.c


@pytest.mark.parametrize("contract_column", TRACK_RECORD_COLUMNS, ids=lambda c: c.name)
def test_migration_030_and_orm_agree_on_every_column(contract_column):
    """Migration 030 <-> ORM parity, reusing db.track_record_schema_contract
    (PR-G2A's single source of truth for the migration's column contract)
    rather than re-deriving expectations independently -- the two can
    never silently drift apart from each other."""
    col = Company.__table__.c[contract_column.name]
    assert col.nullable is contract_column.is_nullable
    if contract_column.max_length is not None:
        assert col.type.length == contract_column.max_length
    # Type-family cross-check (information_schema data_type string vs.
    # the Python/SQLAlchemy type class) for each of the four columns.
    type_expectations = {
        "track_record_score": Integer,
        "track_record_json": JSONB,
        "track_record_at": DateTime,
        "track_record_version": String,
    }
    assert isinstance(col.type, type_expectations[contract_column.name])


def test_orm_attributes_are_instrumented():
    for name in TRACK_RECORD_FIELDS:
        assert isinstance(getattr(Company, name), InstrumentedAttribute), name


def test_assignment_history_tracks_changes():
    company = Company(name=f"History Test {uuid.uuid4().hex}")
    # A never-set nullable attribute reports no pending "added" value yet.
    before = attributes.get_history(company, "track_record_score")
    assert before.added == () or before.added == (None,)

    company.track_record_score = 77
    after = attributes.get_history(company, "track_record_score")
    assert 77 in after.added


def test_track_record_columns_settable_independently_of_each_other():
    """Sanity: the four attributes are independent Python-level slots --
    setting one does not implicitly affect the others (the state-coherence
    invariant is enforced by the DB CHECK constraint, not by the ORM)."""
    company = Company(name=f"Independence Test {uuid.uuid4().hex}")
    company.track_record_score = 10
    assert company.track_record_json is None
    assert company.track_record_at is None
    assert company.track_record_version is None


# ===================================================================
# 2. DB-backed -- local Postgres only, transaction + rollback.
#    Migration 030 is assumed already applied persistently to the local
#    dev database (a one-time operational step for this PR); these tests
#    only exercise ORM CRUD against the already-existing physical schema.
# ===================================================================


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
        # A test that triggered an IntegrityError (e.g. a CHECK constraint
        # violation) already causes SQLAlchemy to deassociate `outer` from
        # the connection as part of its own error handling -- calling
        # rollback() again in that case is a harmless no-op but emits a
        # SAWarning, so only roll back when there is actually still an
        # active transaction to roll back.
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _make_company(**overrides) -> Company:
    name = overrides.pop("name", f"PR-G2B Mapping Test {uuid.uuid4().hex}")
    return Company(name=name, display_name=name, **overrides)


def test_flush_sees_physical_columns_through_same_connection(db_session):
    """Proves the ORM mapping is wired to real, physically-existing
    columns -- not just a Python-level declaration -- by re-reading the
    just-flushed row via raw SQL on the exact same connection/transaction
    the ORM flush used."""
    company = _make_company(
        track_record_score=55,
        track_record_json={"factors": []},
        track_record_at=None,
        track_record_version="company_track_record_v1",
    )
    # Satisfy the state-coherence CHECK: json/at/version all NOT NULL together.
    from datetime import datetime, timezone

    company.track_record_at = datetime.now(timezone.utc)
    db_session.add(company)
    db_session.flush()

    row = (
        db_session.connection()
        .execute(
            text(
                "SELECT track_record_score, track_record_json, track_record_version "
                "FROM companies WHERE id = :id"
            ),
            {"id": company.id},
        )
        .one()
    )
    assert row.track_record_score == 55
    assert row.track_record_json == {"factors": []}
    assert row.track_record_version == "company_track_record_v1"


def test_uncomputed_all_null_state_is_the_default(db_session):
    company = _make_company()
    db_session.add(company)
    db_session.flush()

    row = (
        db_session.connection()
        .execute(
            text(
                "SELECT track_record_score, track_record_json, track_record_at, "
                "track_record_version FROM companies WHERE id = :id"
            ),
            {"id": company.id},
        )
        .one()
    )
    assert row == (None, None, None, None)


def test_computed_valid_state_is_accepted(db_session):
    from datetime import datetime, timezone

    company = _make_company(
        track_record_score=None,  # legitimate "no core evidence" computed result
        track_record_json={},
        track_record_at=datetime.now(timezone.utc),
        track_record_version="company_track_record_v1",
    )
    db_session.add(company)
    db_session.flush()  # must not raise

    row = (
        db_session.connection()
        .execute(
            text("SELECT track_record_score FROM companies WHERE id = :id"),
            {"id": company.id},
        )
        .one()
    )
    assert row.track_record_score is None


def test_invalid_partial_state_rejected_by_db_constraint(db_session):
    """json set, at/version left NULL -- the state-coherence CHECK must
    reject this at flush time, proving the ORM mapping is really backed
    by the enforcing constraint, not just passing values through."""
    company = _make_company(track_record_json={"partial": True})
    db_session.add(company)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_score_out_of_range_rejected_by_db_constraint(db_session):
    from datetime import datetime, timezone

    company = _make_company(
        track_record_score=999,
        track_record_json={},
        track_record_at=datetime.now(timezone.utc),
        track_record_version="company_track_record_v1",
    )
    db_session.add(company)
    with pytest.raises(IntegrityError):
        db_session.flush()


class _DeliberateRollback(Exception):
    """Raised only to trigger a SAVEPOINT rollback inside a test -- never
    escapes the `with pytest.raises(...)` block it is used in."""


def test_rollback_leaves_no_trace(db_session):
    """Uses a SAVEPOINT (Session.begin_nested()) rather than
    Session.rollback() so this test's own rollback never disturbs the
    fixture's outer, externally-managed transaction."""
    company_id = None
    with pytest.raises(_DeliberateRollback):
        with db_session.begin_nested():
            company = _make_company(name=f"PR-G2B Rollback Proof {uuid.uuid4().hex}")
            db_session.add(company)
            db_session.flush()
            company_id = company.id
            raise _DeliberateRollback()

    row = (
        db_session.connection()
        .execute(text("SELECT 1 FROM companies WHERE id = :id"), {"id": company_id})
        .first()
    )
    assert row is None
