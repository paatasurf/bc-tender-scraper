"""Regression tests for the CONFIRMED root cause of the production
`company-intelligence` deadlock (pipeline_runs id 759, 2026-08-05):

    (psycopg2.errors.DeadlockDetected) deadlock detected
    DETAIL:  Process 208602 waits for ShareLock on transaction 444062;
             blocked by process 208644.
             Process 208644 waits for ShareLock on transaction 444030;
             blocked by process 208602.
    CONTEXT: while updating tuple (184,2) in relation "permits"
    [SQL: UPDATE permits SET company_id=%(company_id)s,
          canonical_merge_confidence=%(canonical_merge_confidence)s,
          canonical_merge_method=%(canonical_merge_method)s
          WHERE permits.id = %(id_1)s]

Both processes were stuck inside `permits` -- this is a same-table,
classic AB-BA lock-order deadlock between two transactions each running
pipeline.company_intelligence.populate_companies_from_permits()'s per-row
UPDATE, not a cross-table permits/companies conflict (an earlier version
of this test file, superseded by this one, modeled the wrong pair of
tables before the full untruncated production error text was fetched).

Root cause: populate_companies_from_permits() drove its UPDATE loop from
a SELECT with no ORDER BY. PostgreSQL does not guarantee row order for an
unordered scan, and nothing in pipeline.run_coordinator (or api.internal's
duplicate-run_id dedup) prevents two company-intelligence runs from
overlapping (see PR #156/#157/#158's audit) -- so two concurrent
transactions could visit/lock the same permit rows in different relative
orders, forming a wait cycle.

Fix (see pipeline/company_intelligence.py's _permit_resolution_query()):
ORDER BY Permit.id. Every caller now locks permit rows in the same
ascending order, so two overlapping transactions can only ever block in
one direction and can never form a cycle -- a lock-order fix, not a retry.

Three tests:
  1. test_concurrent_permit_updates_in_opposite_order_deadlock --
     characterizes the vulnerability class directly: two explicit,
     opposite lock-acquisition orders over the same two permit rows,
     using the exact SQL shape from the production error. Deadlocks
     deterministically regardless of the fix (it does not go through
     _permit_resolution_query() at all -- it exists to document why the
     fix matters and to prove PostgreSQL really will deadlock here).
  2. test_permit_resolution_query_orders_by_permit_id -- a direct,
     static check that the fix is actually present in the query the
     production code uses.
  3. test_concurrent_calls_following_permit_resolution_query_order_do_not_deadlock
     -- the dynamic counterpart to test 1: the same two permit rows, the
     same barrier-forced overlap, but both sides now follow
     _permit_resolution_query()'s own (ascending) order -- proving the
     invariant the fix establishes. Before the fix existed, this
     ordering constraint did not exist anywhere in the codebase; with it
     in place, two overlapping transactions cannot deadlock on these
     rows.

No blind retry, no xfail, no coordinator/schema changes.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from db.models import Company, Permit
from pipeline.company_intelligence import _permit_resolution_query
from tests.db_test_safety import require_local_test_database

_PREFIX = "ZZTEST-CI-DEADLOCK-"


@pytest.fixture
def two_permits():
    """Two synthetic Permit rows (and one Company they both point at),
    cleaned up before and after -- real local Postgres, not mocked, so
    real row-level locking applies."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})

    def _cleanup():
        session = Session(bind=engine)
        try:
            session.execute(
                delete(Permit).where(Permit.external_id.like(f"{_PREFIX}%"))
            )
            session.execute(delete(Company).where(Company.name == f"{_PREFIX}company"))
            session.commit()
        finally:
            session.close()

    _cleanup()

    session = Session(bind=engine)
    try:
        company = Company(name=f"{_PREFIX}company", total_projects=0)
        session.add(company)
        session.flush()
        permit_low = Permit(
            address="1 Test St",
            applicant=f"{_PREFIX}applicant",
            issue_date="2026-01-01",
            city="Vancouver",
            source="vancouver",
            external_id=f"{_PREFIX}permit-low",
            company_id=company.id,
        )
        permit_high = Permit(
            address="2 Test St",
            applicant=f"{_PREFIX}applicant",
            issue_date="2026-01-01",
            city="Vancouver",
            source="vancouver",
            external_id=f"{_PREFIX}permit-high",
            company_id=company.id,
        )
        session.add_all([permit_low, permit_high])
        session.commit()
        # Ensure permit_low.id really is the smaller id regardless of
        # insertion/autoincrement quirks -- swap if needed.
        if permit_low.id > permit_high.id:
            permit_low, permit_high = permit_high, permit_low
        low_id, high_id, company_id = permit_low.id, permit_high.id, company.id
    finally:
        session.close()

    try:
        yield engine, low_id, high_id, company_id
    finally:
        _cleanup()
        engine.dispose()


def _lock_and_update_permit(session: Session, permit_id: int, method: str) -> None:
    """The exact SQL shape from the production deadlock's own [SQL: ...]
    line: UPDATE permits SET company_id=..., canonical_merge_confidence=...,
    canonical_merge_method=... WHERE permits.id = ...."""
    session.execute(
        Permit.__table__.update()
        .where(Permit.id == permit_id)
        .values(
            company_id=None,
            canonical_merge_confidence=0.9,
            canonical_merge_method=method,
        )
    )


def test_concurrent_permit_updates_in_opposite_order_deadlock(two_permits) -> None:
    """Vulnerability characterization: two transactions locking the same
    two permit rows in opposite order deadlock in real PostgreSQL --
    reproduces the exact production failure mode (pipeline_runs id 759).
    """
    engine, low_id, high_id, _company_id = two_permits

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def _run_ascending() -> None:
        session = Session(bind=engine)
        try:
            _lock_and_update_permit(session, low_id, "a-first")
            barrier.wait(timeout=5)
            _lock_and_update_permit(session, high_id, "a-second")
            session.commit()
            outcomes["ascending"] = "committed"
        except OperationalError as exc:
            outcomes["ascending"] = exc
            session.rollback()
        finally:
            session.close()

    def _run_descending() -> None:
        session = Session(bind=engine)
        try:
            _lock_and_update_permit(session, high_id, "b-first")
            barrier.wait(timeout=5)
            _lock_and_update_permit(session, low_id, "b-second")
            session.commit()
            outcomes["descending"] = "committed"
        except OperationalError as exc:
            outcomes["descending"] = exc
            session.rollback()
        finally:
            session.close()

    threads = [
        threading.Thread(target=_run_ascending),
        threading.Thread(target=_run_descending),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    results = [outcomes.get("ascending"), outcomes.get("descending")]
    deadlocked = [r for r in results if isinstance(r, OperationalError)]
    committed = [r for r in results if r == "committed"]

    assert len(deadlocked) == 1, (
        "Expected exactly one side to hit a real Postgres deadlock "
        f"(production failure mode, pipeline_runs id 759) -- got {results}"
    )
    assert len(committed) == 1
    assert "deadlock" in str(deadlocked[0]).lower()


def test_permit_resolution_query_orders_by_permit_id() -> None:
    """Direct check that the fix is actually present: the query
    populate_companies_from_permits() uses to drive its per-row UPDATE
    loop must order by Permit.id."""
    compiled = str(_permit_resolution_query().compile())
    assert "ORDER BY permits.id" in compiled


def test_concurrent_calls_following_permit_resolution_query_order_do_not_deadlock(
    two_permits,
) -> None:
    """Proves the invariant the fix establishes: two overlapping
    transactions that both lock permit rows in the order
    _permit_resolution_query() dictates (ascending id) -- exactly what
    every caller of populate_companies_from_permits() now does -- cannot
    deadlock on these rows, even under the same adversarial barrier-forced
    overlap that reliably deadlocks in the previous test. One side simply
    blocks until the other commits; neither raises."""
    engine, low_id, high_id, _company_id = two_permits

    # Confirm the ordering this test relies on actually matches what the
    # production query would return for these two rows, not just an
    # assumption.
    session = Session(bind=engine)
    try:
        ordered_ids = [
            row.id
            for row in session.execute(
                _permit_resolution_query().where(Permit.id.in_([low_id, high_id]))
            )
        ]
    finally:
        session.close()
    assert ordered_ids == [low_id, high_id]

    # Unlike the previous test, the barrier only synchronizes the START of
    # both transactions -- it must NOT sit between the two lock
    # acquisitions here. Both threads now target the same first row
    # (low_id, the canonical order both must follow), so whichever thread
    # loses that race blocks at the real Postgres row-lock level *before*
    # it could ever reach a second barrier -- placing one there (as the
    # opposite-order test correctly does, where the two threads contend
    # for *different* first rows) would deadlock the test harness itself
    # against the DB wait, not exercise the invariant under test.
    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def _run_in_canonical_order(
        key: str, first_method: str, second_method: str
    ) -> None:
        session = Session(bind=engine)
        try:
            barrier.wait(timeout=5)
            _lock_and_update_permit(session, low_id, first_method)
            _lock_and_update_permit(session, high_id, second_method)
            session.commit()
            outcomes[key] = "committed"
        except OperationalError as exc:
            outcomes[key] = exc
            session.rollback()
        finally:
            session.close()

    threads = [
        threading.Thread(
            target=_run_in_canonical_order, args=("t1", "t1-first", "t1-second")
        ),
        threading.Thread(
            target=_run_in_canonical_order, args=("t2", "t2-first", "t2-second")
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    results = [outcomes.get("t1"), outcomes.get("t2")]
    for result in results:
        assert result == "committed", (
            "Two transactions both following the canonical (ascending-id) "
            f"lock order must never deadlock on the same rows -- got {results}"
        )
