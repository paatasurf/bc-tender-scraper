"""Tests for the PR-G3.2 track-record shadow backfill orchestration
(pipeline/track_record_backfill.py).

Sections:
  1. Argument validation -- no DB, no session touched (validation raises
     before ``_select_companies`` is ever called).
  2. DB-backed tests (local Postgres only). Uses SQLAlchemy's documented
     "join a Session into an external transaction" pattern
     (``Session(bind=connection, join_transaction_mode="create_savepoint")``)
     so the code under test's own internal ``session.commit()``/
     ``session.rollback()`` calls only ever create/release/roll back a
     SAVEPOINT -- the outer, test-owned transaction (started via
     ``connection.begin()``) is never actually committed, and is always
     rolled back at fixture teardown. Migration 030 itself is assumed
     already applied to the local dev database (established by earlier
     PRs in this series) -- these tests never apply it themselves.
  3. Structural: no API/LLM/network access anywhere in the module source.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.scoring.company_track_record import COMPANY_TRACK_RECORD_ALGORITHM_VERSION
from pipeline.track_record_backfill import (
    STAGE_ADAPTER,
    STAGE_ASSIGNMENT,
    STAGE_COMMIT,
    STAGE_IDENTITY,
    TrackRecordBackfillError,
    backfill_company_track_records,
)
from tests.db_test_safety import require_local_test_database

MODULE_FILE = (
    Path(__file__).resolve().parents[2] / "pipeline" / "track_record_backfill.py"
)

REFERENCE_DATE = date(2026, 1, 1)
COMPUTED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# ===================================================================
# 1. Argument validation -- no DB, session never touched
# ===================================================================


@pytest.mark.parametrize("bad_company_ids", ["abc", 5, {1, 2}, {"a": 1}, 5.0])
def test_invalid_company_ids_container_raises_before_touching_session(
    bad_company_ids,
):
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, company_ids=bad_company_ids)


def test_company_ids_with_non_int_element_raises():
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, company_ids=[1, "2", 3])


def test_company_ids_with_bool_element_raises():
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, company_ids=[1, True, 3])


@pytest.mark.parametrize("bad_sample_size", [-1, "5", 5.0, True, False])
def test_invalid_sample_size_raises_before_touching_session(bad_sample_size):
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, sample_size=bad_sample_size)


def test_reference_date_datetime_instead_of_date_raises():
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, reference_date=datetime.now(timezone.utc))


def test_reference_date_wrong_type_raises():
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, reference_date="2026-01-01")


def test_computed_at_naive_raises():
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, computed_at=datetime(2026, 1, 1))


def test_computed_at_wrong_type_raises():
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, computed_at="2026-01-01")


@pytest.mark.parametrize("bad_dry_run", [1, 0, "true", "False", None, [], "yes"])
def test_invalid_dry_run_raises_before_touching_session(bad_dry_run):
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, dry_run=bad_dry_run)


@pytest.mark.parametrize("bad_force", [1, 0, "true", "False", None, [], "yes"])
def test_invalid_force_raises_before_touching_session(bad_force):
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(None, force=bad_force)


class _SessionSpy:
    """Any attribute access raises -- used to prove validation genuinely
    happens before any selection/session work, not just incidentally
    (passing plain ``None`` as the session would also "work" for this
    purpose, but only because ``None.scalars`` happens to raise a
    different, unrelated exception -- this spy makes the "session is
    never touched" assertion explicit and unambiguous)."""

    def __getattr__(self, name):
        raise AssertionError(
            f"session.{name} must never be touched when validation fails"
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"company_ids": "abc"},
        {"sample_size": -1},
        {"dry_run": "not-a-bool"},
        {"force": "not-a-bool"},
        {"reference_date": datetime.now(timezone.utc)},
        {"computed_at": datetime(2026, 1, 1)},
    ],
    ids=[
        "company_ids",
        "sample_size",
        "dry_run",
        "force",
        "reference_date",
        "computed_at",
    ],
)
def test_validation_failure_never_touches_session_object(kwargs):
    with pytest.raises(TrackRecordBackfillError):
        backfill_company_track_records(_SessionSpy(), **kwargs)


# ===================================================================
# 2. DB-backed -- local Postgres only.
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
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _make_company(session: Session, **overrides) -> Company:
    name = overrides.pop("name", f"PR-G3.2 Backfill Test {uuid.uuid4().hex}")
    company = Company(name=name, display_name=name, **overrides)
    session.add(company)
    return company


def _row(session: Session, company_id: int):
    return (
        session.connection()
        .execute(
            text(
                "SELECT track_record_score, track_record_json, track_record_at, "
                "track_record_version FROM companies WHERE id = :id"
            ),
            {"id": company_id},
        )
        .one()
    )


# --- Selection predicate + ordering ---------------------------------


def test_selection_excludes_current_version_includes_null_and_stale(db_session):
    fresh = _make_company(db_session, total_projects=1, award_count=0)
    stale = _make_company(db_session, total_projects=1, award_count=0)
    current = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()

    stale.track_record_score = 10
    stale.track_record_json = {"x": 1}
    stale.track_record_at = COMPUTED_AT
    stale.track_record_version = "company_track_record_v0_old"

    current.track_record_score = 20
    current.track_record_json = {"y": 2}
    current.track_record_at = COMPUTED_AT
    current.track_record_version = COMPANY_TRACK_RECORD_ALGORITHM_VERSION
    db_session.flush()

    ids = [fresh.id, stale.id, current.id]
    result = backfill_company_track_records(
        db_session,
        company_ids=ids,
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    selected_ids = {r["company_id"] for r in result["results"]}
    assert selected_ids == {fresh.id, stale.id}
    assert current.id not in selected_ids
    assert result["selected"] == 2


def test_deterministic_ordering_by_ascending_id(db_session):
    c1 = _make_company(db_session, total_projects=1, award_count=0)
    c2 = _make_company(db_session, total_projects=1, award_count=0)
    c3 = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()
    ids_in_reverse = [c3.id, c1.id, c2.id]

    result = backfill_company_track_records(
        db_session,
        company_ids=ids_in_reverse,
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    result_ids = [r["company_id"] for r in result["results"]]
    assert result_ids == sorted([c1.id, c2.id, c3.id])


def test_sample_size_applied_after_filter_keeps_smallest_ids(db_session):
    companies = [
        _make_company(db_session, total_projects=1, award_count=0) for _ in range(5)
    ]
    db_session.flush()
    ids = sorted(c.id for c in companies)

    result = backfill_company_track_records(
        db_session,
        company_ids=ids,
        sample_size=2,
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["selected"] == 2
    result_ids = [r["company_id"] for r in result["results"]]
    assert result_ids == ids[:2]


def test_company_ids_restricts_selection(db_session):
    target = _make_company(db_session, total_projects=1, award_count=0)
    other = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()

    result = backfill_company_track_records(
        db_session,
        company_ids=[target.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["selected"] == 1
    assert result["results"][0]["company_id"] == target.id
    assert other.track_record_version is None


def test_company_ids_none_applies_no_id_filter(db_session):
    """company_ids=None must not be conflated with company_ids=[] -- it
    applies no ID filter at all (still scoped by the eligibility filter)."""
    a = _make_company(db_session, total_projects=1, award_count=0)
    b = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()

    result = backfill_company_track_records(
        db_session, reference_date=REFERENCE_DATE, computed_at=COMPUTED_AT
    )
    selected_ids = {r["company_id"] for r in result["results"]}
    assert {a.id, b.id} <= selected_ids


def test_company_ids_empty_list_selects_zero(db_session):
    """An explicit, non-None empty list must select zero rows -- distinct
    from company_ids=None (no filter)."""
    _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()

    result = backfill_company_track_records(
        db_session,
        company_ids=[],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["selected"] == 0
    assert result["processed"] == 0
    assert result["persisted"] == 0
    assert result["results"] == []


def test_sample_size_uses_sql_limit_not_python_only_slicing(db_session):
    companies = [
        _make_company(db_session, total_projects=1, award_count=0) for _ in range(3)
    ]
    db_session.flush()
    ids = sorted(c.id for c in companies)

    captured: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    connection = db_session.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        result = backfill_company_track_records(
            db_session,
            company_ids=ids,
            sample_size=2,
            reference_date=REFERENCE_DATE,
            computed_at=COMPUTED_AT,
        )
    finally:
        event.remove(connection, "before_cursor_execute", capture)

    assert result["selected"] == 2
    select_statements = [
        s
        for s in captured
        if s.strip().upper().startswith("SELECT") and "companies" in s
    ]
    assert select_statements, "expected at least one SELECT companies statement"
    assert any("LIMIT" in s.upper() for s in select_statements)


def test_selection_does_not_autoflush_unrelated_pending_object(db_session):
    """The selection SELECT must run inside session.no_autoflush -- an
    unrelated pending object that would violate a constraint if flushed
    must never be flushed just because the selection query ran. Uses
    dry_run so nothing else in the function ever calls flush()/commit()
    either, isolating this from the (expected, unrelated) fact that a
    real commit legitimately flushes everything pending in the session."""
    existing = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()
    target = _make_company(db_session, total_projects=2, award_count=0)
    db_session.flush()

    duplicate = Company(
        name=existing.name,
        display_name=existing.name,
        total_projects=99,
        award_count=0,
    )
    db_session.add(duplicate)

    result = backfill_company_track_records(
        db_session,
        company_ids=[target.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
        dry_run=True,
    )
    assert result["selected"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0

    db_session.expunge(duplicate)


def test_force_recomputes_already_current_version(db_session):
    company = _make_company(db_session, total_projects=5, award_count=0)
    db_session.flush()
    cid = company.id

    first = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert first["selected"] == 1
    assert first["persisted"] == 1

    without_force = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert without_force["selected"] == 0

    with_force = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
        force=True,
    )
    assert with_force["selected"] == 1
    assert with_force["persisted"] == 1


def test_second_default_run_recomputes_nothing(db_session):
    company = _make_company(
        db_session, total_projects=5, award_count=2, award_clients=["City of Vancouver"]
    )
    db_session.flush()
    cid = company.id

    first = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert first["selected"] == 1
    assert first["persisted"] == 1

    second = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert second["selected"] == 0
    assert second["processed"] == 0
    assert second["persisted"] == 0
    assert second["errors"] == []


# --- Reference date / computed_at contract ---------------------------


def test_default_reference_date_and_computed_at_are_utc_now(db_session):
    company = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()
    cid = company.id

    before = datetime.now(timezone.utc)
    result = backfill_company_track_records(db_session, company_ids=[cid])
    after = datetime.now(timezone.utc)

    reference_date = date.fromisoformat(result["reference_date"])
    assert before.date() <= reference_date <= after.date()
    computed_at = datetime.fromisoformat(result["computed_at"])
    assert computed_at.tzinfo is not None
    assert before <= computed_at <= after


def test_single_reference_date_and_computed_at_shared_across_batch(db_session):
    c1 = _make_company(db_session, total_projects=1, award_count=0)
    c2 = _make_company(db_session, total_projects=2, award_count=0)
    c3 = _make_company(db_session, total_projects=3, award_count=0)
    db_session.flush()
    ids = [c1.id, c2.id, c3.id]

    result = backfill_company_track_records(
        db_session,
        company_ids=ids,
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["persisted"] == 3
    assert result["reference_date"] == REFERENCE_DATE.isoformat()
    assert result["computed_at"] == COMPUTED_AT.isoformat()
    for company in (c1, c2, c3):
        assert company.track_record_at == COMPUTED_AT
        assert company.track_record_json["reference_date"] == REFERENCE_DATE.isoformat()


# --- Success commit ---------------------------------------------------


def test_success_commit_persists_and_is_visible_through_same_connection(db_session):
    company = _make_company(
        db_session,
        total_projects=5,
        award_count=2,
        award_clients=["City of Vancouver", "City of Burnaby"],
    )
    db_session.flush()
    cid = company.id

    result = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["persisted"] == 1
    assert result["failed"] == 0

    row = _row(db_session, cid)
    assert row.track_record_version == "company_track_record_v1"
    assert row.track_record_at == COMPUTED_AT
    assert row.track_record_score is not None


# --- score=None coherent computed state -------------------------------


def test_score_none_result_is_persisted_as_coherent_computed_state(db_session):
    company = _make_company(db_session, total_projects=0, award_count=0)
    db_session.flush()
    cid = company.id

    result = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["failed"] == 0
    assert result["persisted"] == 1

    row = _row(db_session, cid)
    assert row.track_record_score is None
    assert row.track_record_json is not None
    assert row.track_record_at is not None
    assert row.track_record_version == "company_track_record_v1"


# --- dry_run ------------------------------------------------------------


def test_dry_run_does_not_mutate_and_reports_predicted_results(db_session):
    company = _make_company(db_session, total_projects=5, award_count=0)
    db_session.flush()
    cid = company.id

    result = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["selected"] == 1
    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert result["persisted"] == 0
    assert result["failed"] == 0
    assert result["results"][0]["status"] == "dry_run_computed"
    assert result["results"][0]["score"] is not None

    assert company.track_record_version is None
    assert company.track_record_score is None
    assert company.track_record_json is None
    assert company.track_record_at is None

    row = _row(db_session, cid)
    assert row.track_record_version is None

    # And a real run afterward still sees it as eligible -- nothing was
    # committed by the dry run.
    real = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert real["selected"] == 1
    assert real["persisted"] == 1


def test_dry_run_never_calls_commit(db_session, monkeypatch):
    """dry_run must never call session.commit(). session.flush() is not
    asserted here: SQLAlchemy's own autoflush mechanism calls flush()
    before any query (including the selection SELECT below) regardless of
    dry_run -- that is ordinary ORM behavior, not something this
    orchestration code invokes itself. The absence of any persisted
    mutation is proven directly (by re-querying the row) in
    test_dry_run_does_not_mutate_and_reports_predicted_results."""
    company = _make_company(db_session, total_projects=5, award_count=0)
    db_session.flush()
    cid = company.id

    def boom(*_args, **_kwargs):
        raise AssertionError("dry_run must never call commit")

    monkeypatch.setattr(db_session, "commit", boom)

    result = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
        dry_run=True,
    )
    assert result["failed"] == 0
    assert result["skipped"] == 1


# --- Rollback-and-continue, non-cascading failures ---------------------


class _PoisonedIdentity:
    """A selected "company" whose .id itself raises -- proves an identity
    read failure is caught inside the protected per-company block, not
    left to propagate out of the batch."""

    @property
    def id(self):
        raise RuntimeError("simulated identity read failure")


def test_identity_read_failure_does_not_abort_batch(db_session, monkeypatch):
    good_before = _make_company(db_session, total_projects=1, award_count=0)
    good_after = _make_company(db_session, total_projects=2, award_count=0)
    db_session.flush()

    import pipeline.track_record_backfill as backfill_mod

    poisoned = _PoisonedIdentity()

    def fake_select_companies(*_args, **_kwargs):
        return [good_before, poisoned, good_after]

    monkeypatch.setattr(backfill_mod, "_select_companies", fake_select_companies)

    result = backfill_company_track_records(
        db_session, reference_date=REFERENCE_DATE, computed_at=COMPUTED_AT
    )
    assert result["selected"] == 3
    assert result["failed"] == 1
    assert result["persisted"] == 2

    (error,) = [e for e in result["errors"] if e["company_id"] is None]
    assert error["stage"] == STAGE_IDENTITY
    assert error["error_type"] == "RuntimeError"
    assert set(error.keys()) == {"company_id", "stage", "error_type"}

    row_before = _row(db_session, good_before.id)
    row_after = _row(db_session, good_after.id)
    assert row_before.track_record_version == "company_track_record_v1"
    assert row_after.track_record_version == "company_track_record_v1"


def test_rollback_and_continue_after_adapter_failure_no_cascade(db_session):
    good_before = _make_company(db_session, total_projects=2, award_count=0)
    bad = _make_company(db_session, total_projects=1, award_count=0)
    good_after = _make_company(db_session, total_projects=3, award_count=0)
    db_session.flush()

    # A structurally invalid total_projects the ORM never rejects at
    # write time (no CHECK constraint on this column) -- trips the
    # adapter's fail-closed count validation. Bypasses the ORM, so the
    # already-loaded in-memory attribute is explicitly expired to force a
    # fresh read, matching what a genuinely corrupted row would look like.
    db_session.connection().execute(
        text("UPDATE companies SET total_projects = -5 WHERE id = :id"),
        {"id": bad.id},
    )
    db_session.expire(bad)

    result = backfill_company_track_records(
        db_session,
        company_ids=[good_before.id, bad.id, good_after.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["failed"] == 1
    assert result["persisted"] == 2
    assert result["selected"] == 3
    (error,) = [e for e in result["errors"] if e["company_id"] == bad.id]
    assert error["stage"] == STAGE_ADAPTER
    assert error["error_type"]

    row_before = _row(db_session, good_before.id)
    row_after = _row(db_session, good_after.id)
    assert row_before.track_record_version == "company_track_record_v1"
    assert row_after.track_record_version == "company_track_record_v1"


def test_rollback_and_continue_after_assignment_failure(db_session, monkeypatch):
    good_before = _make_company(db_session, total_projects=2, award_count=0)
    bad = _make_company(db_session, total_projects=1, award_count=0)
    good_after = _make_company(db_session, total_projects=3, award_count=0)
    db_session.flush()
    bad_id = bad.id

    import pipeline.track_record_backfill as backfill_mod

    original_assign = backfill_mod.assign_track_record_result

    def flaky_assign(company, result, *, computed_at):
        if company.id == bad_id:
            raise RuntimeError("simulated assignment failure")
        return original_assign(company, result, computed_at=computed_at)

    monkeypatch.setattr(backfill_mod, "assign_track_record_result", flaky_assign)

    result = backfill_company_track_records(
        db_session,
        company_ids=[good_before.id, bad_id, good_after.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["failed"] == 1
    assert result["persisted"] == 2
    (error,) = [e for e in result["errors"] if e["company_id"] == bad_id]
    assert error["stage"] == STAGE_ASSIGNMENT
    assert error["error_type"] == "RuntimeError"

    row_before = _row(db_session, good_before.id)
    row_after = _row(db_session, good_after.id)
    assert row_before.track_record_version == "company_track_record_v1"
    assert row_after.track_record_version == "company_track_record_v1"


def test_rollback_and_continue_after_commit_failure(db_session, monkeypatch):
    bad = _make_company(db_session, total_projects=1, award_count=0)
    good = _make_company(db_session, total_projects=2, award_count=0)
    db_session.flush()
    # Establish a stable SAVEPOINT checkpoint (via a real commit, safely
    # contained inside the fixture's own outer transaction) *before*
    # patching commit() to fail. Without this, "bad" being the very first
    # company processed means its own session.rollback() would revert all
    # the way back to session-start -- undoing this arrange-phase insert
    # of both companies, not just the failed assignment.
    db_session.commit()

    original_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated commit failure")
        return original_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    result = backfill_company_track_records(
        db_session,
        company_ids=[bad.id, good.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["failed"] == 1
    assert result["persisted"] == 1
    (error,) = [e for e in result["errors"] if e["company_id"] == bad.id]
    assert error["stage"] == STAGE_COMMIT
    assert error["error_type"] == "RuntimeError"

    row_good = _row(db_session, good.id)
    assert row_good.track_record_version == "company_track_record_v1"


def test_error_entries_are_fail_closed_shape_only(db_session):
    """The error contract is fail-closed by construction: only
    company_id/stage/error_type keys ever exist -- there is no message
    field at all for a raw str(exc) (or anything derived from it) to leak
    through in the first place."""
    bad = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()
    db_session.connection().execute(
        text("UPDATE companies SET total_projects = -1 WHERE id = :id"),
        {"id": bad.id},
    )
    # The raw-SQL UPDATE above bypasses the ORM, so the already-loaded
    # in-memory attribute would otherwise stay stale at its original
    # value -- expire it so the next read genuinely reflects the
    # corrupted row, matching what a fresh session would see.
    db_session.expire(bad)

    result = backfill_company_track_records(
        db_session,
        company_ids=[bad.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["failed"] == 1
    (error,) = result["errors"]
    assert set(error.keys()) == {"company_id", "stage", "error_type"}
    assert error["stage"] == STAGE_ADAPTER
    assert error["error_type"] == "CompanyTrackRecordAdapterError"


def test_exception_with_embedded_secret_never_leaks_into_errors(
    db_session, monkeypatch
):
    """Even if a lower layer's exception message happens to embed a
    connection URL, password, API key, or raw row payload, none of that
    text can ever reach the run result -- the error contract carries only
    a fixed stage name and the exception's class name, never its
    message."""
    company = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()
    cid = company.id

    secret = (
        "postgresql://scraper_user:S3cr3tPassw0rd@db.internal:5432/prod "
        "ANTHROPIC_API_KEY=sk-fake-secret-1234 company_payload={'name': 'Acme'}"
    )

    import pipeline.track_record_backfill as backfill_mod

    def leaky_assign(company, result, *, computed_at):
        raise RuntimeError(secret)

    monkeypatch.setattr(backfill_mod, "assign_track_record_result", leaky_assign)

    result = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["failed"] == 1
    (error,) = result["errors"]
    assert error["stage"] == STAGE_ASSIGNMENT
    assert error["error_type"] == "RuntimeError"
    assert set(error.keys()) == {"company_id", "stage", "error_type"}

    serialized = repr(result)
    assert "S3cr3tPassw0rd" not in serialized
    assert "postgresql://" not in serialized
    assert "sk-fake-secret" not in serialized
    assert "Acme" not in serialized


def test_non_utc_computed_at_normalized_consistently_in_report_and_persisted(
    db_session,
):
    company = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()
    cid = company.id

    local_tz = timezone(timedelta(hours=-8))
    local_computed_at = datetime(2026, 1, 1, 4, 0, tzinfo=local_tz)  # 12:00 UTC
    expected_utc = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    result = backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=local_computed_at,
    )
    assert result["computed_at"] == expected_utc.isoformat()

    row = _row(db_session, cid)
    assert row.track_record_at == expected_utc


# --- Diagnostics aggregation ---------------------------------------------


def test_diagnostics_notes_aggregated_across_batch(db_session):
    dirty = _make_company(
        db_session, total_projects=1, award_count=0, first_project_date="not-a-date"
    )
    clean = _make_company(db_session, total_projects=1, award_count=0)
    db_session.flush()

    result = backfill_company_track_records(
        db_session,
        company_ids=[dirty.id, clean.id],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )
    assert result["diagnostics_notes_count"] >= 1
    dirty_result = next(r for r in result["results"] if r["company_id"] == dirty.id)
    clean_result = next(r for r in result["results"] if r["company_id"] == clean.id)
    assert dirty_result["diagnostics_notes"] >= 1
    assert clean_result["diagnostics_notes"] == 0


# --- Legacy fields untouched ----------------------------------------------


def test_legacy_and_derived_fields_unchanged_after_backfill(db_session):
    company = _make_company(
        db_session,
        total_projects=5,
        award_count=2,
        award_clients=["City of Vancouver"],
        ai_reliability_score=77,
        ai_summary="Reliable firm.",
        construction_score=55,
        cip_json={"a": 1},
        capability_profile_json={"b": 2},
        google_rating=4.4,
        google_reviews_count=12,
    )
    db_session.flush()
    cid = company.id

    backfill_company_track_records(
        db_session,
        company_ids=[cid],
        reference_date=REFERENCE_DATE,
        computed_at=COMPUTED_AT,
    )

    assert company.ai_reliability_score == 77
    assert company.ai_summary == "Reliable firm."
    assert company.construction_score == 55
    assert company.cip_json == {"a": 1}
    assert company.capability_profile_json == {"b": 2}
    assert company.google_rating == 4.4
    assert company.google_reviews_count == 12
    assert company.total_projects == 5
    assert company.award_count == 2
    assert company.award_clients == ["City of Vancouver"]


# ===================================================================
# 3. Structural: no network/API/LLM access anywhere in the module
# ===================================================================


def test_module_source_never_references_network_llm_or_api_keys():
    source = MODULE_FILE.read_text(encoding="utf-8")
    forbidden_substrings = (
        "import requests",
        "import anthropic",
        "ANTHROPIC_API_KEY",
        "GOOGLE_PLACES_API_KEY",
        "GOOGLE_MAPS_API_KEY",
        "requests.post",
        "requests.get",
        "get_anthropic_api_key",
    )
    for forbidden in forbidden_substrings:
        assert (
            forbidden not in source
        ), f"forbidden pattern found in module: {forbidden!r}"


def test_module_not_wired_into_internal_steps_api_or_scheduler():
    internal_steps_source = (MODULE_FILE.parent / "internal_steps.py").read_text(
        encoding="utf-8"
    )
    assert "track_record_backfill" not in internal_steps_source
    assert "backfill_company_track_records" not in internal_steps_source
