"""Diagnostic regression test for the CONFIRMED root cause behind the
production `company-intelligence` deadlock (pipeline_runs id 759,
2026-08-05: psycopg2.errors.DeadlockDetected, "Process ... waits for
ShareLock on transaction ...; blocked by process ...").

Root cause (static audit, see PR description / audit report): the
`company-intelligence` step has NO concurrency guard of any kind --
unlike `scrape-federal`/`scrape-merx-arch`/`scrape-commercial`, it is not
part of pipeline.run_coordinator's TENDER_SCRAPE_STEPS scope, so
run_coordinator_postgres.py's lease-based single-active-run lock (PR #156)
never applies to it. api.internal._enqueue_step()'s duplicate-run_id
dedup (PR #157) only helps when two triggers share the exact same
explicit run_id -- a manual/n8n retrigger with no run_id (the common
case for this endpoint) generates a fresh UUID each time and is NOT
deduplicated. Nothing prevents two company-intelligence runs -- or one
company-intelligence run racing pipeline.populate_companies_from_awards
or any other writer of `permits.company_id` / `companies` rows -- from
executing concurrently.

This test proves that hazard is real, not theoretical: two sessions
updating the same (permit, company) pair in opposite lock order --
exactly what pipeline.company_intelligence.populate_companies_from_permits()
does per-row (Permit row first, then the aggregated Company row), and what
a second concurrent run touching the same rows in a different iteration
order could easily do -- reliably deadlocks in real PostgreSQL.

This is a DIAGNOSTIC test proving the gap exists. It intentionally does
NOT retry, does NOT xfail, and does NOT touch pipeline/run_coordinator*.py,
pipeline/company_intelligence.py, or any other production code -- per
explicit instruction, no fix is proposed until root cause for the
SEPARATE "psycopg2.InterfaceError: cursor already closed" symptom is
confirmed (see audit report: 5 targeted dynamic reproduction attempts of
the yield_per()+interleaved-commit/rollback hypothesis all failed under
this SQLAlchemy 2.0 + psycopg2 configuration, which does not open a real
server-side cursor for yield_per() -- confirmed empty `pg_cursors` during
iteration).
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from db.models import Company, Permit
from tests.db_test_safety import require_local_test_database

_PREFIX = "ZZTEST-CI-DEADLOCK-"


@pytest.fixture
def two_rows(monkeypatch: pytest.MonkeyPatch):
    """One synthetic Permit + one synthetic Company, cleaned up before and
    after -- real local Postgres, not mocked, so real row-level locking
    applies."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})

    def _cleanup():
        session = Session(bind=engine)
        try:
            session.execute(
                delete(Permit).where(Permit.external_id == f"{_PREFIX}permit")
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
        permit = Permit(
            address="1 Test St",
            applicant=f"{_PREFIX}applicant",
            issue_date="2026-01-01",
            city="Vancouver",
            source="vancouver",
            external_id=f"{_PREFIX}permit",
            company_id=company.id,
        )
        session.add(permit)
        session.commit()
        permit_id, company_id = permit.id, company.id
    finally:
        session.close()

    try:
        yield engine, permit_id, company_id
    finally:
        _cleanup()
        engine.dispose()


def test_concurrent_permit_then_company_updates_in_opposite_order_deadlock(
    two_rows,
) -> None:
    """Reproduces the exact production failure mode: two sessions each
    locking the same permit row and the same company row, in opposite
    order, deadlock in real PostgreSQL because nothing serializes
    concurrent company-intelligence-shaped writers. If this test ever
    starts passing without a DeadlockDetected/OperationalError, either
    Postgres's deadlock detector behavior changed or a lock (coordinator
    scope, advisory lock, row-lock ordering) was added elsewhere -- in
    which case this test's premise (there is currently NO such lock)
    should be re-examined, not silenced."""
    engine, permit_id, company_id = two_rows

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def _run_a() -> None:
        session = Session(bind=engine)
        try:
            session.execute(
                Permit.__table__.update()
                .where(Permit.id == permit_id)
                .values(canonical_merge_method="a")
            )
            barrier.wait(timeout=5)
            session.execute(
                Company.__table__.update()
                .where(Company.id == company_id)
                .values(total_projects=1)
            )
            session.commit()
            outcomes["a"] = "committed"
        except OperationalError as exc:
            outcomes["a"] = exc
            session.rollback()
        finally:
            session.close()

    def _run_b() -> None:
        session = Session(bind=engine)
        try:
            session.execute(
                Company.__table__.update()
                .where(Company.id == company_id)
                .values(total_projects=2)
            )
            barrier.wait(timeout=5)
            session.execute(
                Permit.__table__.update()
                .where(Permit.id == permit_id)
                .values(canonical_merge_method="b")
            )
            session.commit()
            outcomes["b"] = "committed"
        except OperationalError as exc:
            outcomes["b"] = exc
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=_run_a), threading.Thread(target=_run_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    results = [outcomes.get("a"), outcomes.get("b")]
    deadlocked = [r for r in results if isinstance(r, OperationalError)]
    committed = [r for r in results if r == "committed"]

    assert len(deadlocked) == 1, (
        "Expected exactly one side to hit a real Postgres deadlock "
        f"(this is the production failure mode, pipeline_runs id 759) -- got {results}"
    )
    assert len(committed) == 1
    assert "deadlock" in str(deadlocked[0]).lower()
