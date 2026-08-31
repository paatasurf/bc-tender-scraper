"""Real local-Postgres tests for pipeline/company_enrichment/orchestrator.py's
in-flight dedup (RFC S7 step 3, golden case #5): a concurrent second
request for the same company_id must get the SAME run_id back, never a
second company_enrichment_jobs row -- relies on
ux_company_enrichment_jobs_company_active (partial unique index on
company_id WHERE status='running').

Same fixture pattern as test_company_enrichment_cache.py / this repo's
other real-Postgres dedup tests (e.g. tests/unit/test_pipeline_runs_dedup.py).
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.company_enrichment_ddl import company_enrichment_migration_statements
from db.models import Company
from pipeline.company_enrichment.orchestrator import (
    find_active_job,
    reclaim_stale_job,
    start_or_join_job,
)
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def enrichment_db():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in company_enrichment_migration_statements():
            conn.execute(text(statement))

    with Session(engine) as session:
        company = Company(name="Dedup Test Co Ltd")
        session.add(company)
        session.commit()
        company_id = company.id

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM company_enrichment_fields WHERE company_id = :id"),
                {"id": company_id},
            )
            conn.execute(
                text("DELETE FROM company_enrichment_jobs WHERE company_id = :id"),
                {"id": company_id},
            )
            conn.execute(
                text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
            )

    try:
        yield engine, company_id
    finally:
        _reset()
        engine.dispose()


def _job_count(engine, company_id: int) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM company_enrichment_jobs WHERE company_id = :id"),
            {"id": company_id},
        ).scalar_one()


def test_first_request_starts_a_new_running_job(enrichment_db) -> None:
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, joined = start_or_join_job(session, company_id, trigger="profile_view")

    assert joined is False
    assert run_id
    assert _job_count(engine, company_id) == 1


def test_second_concurrent_request_for_the_same_company_joins_not_duplicates(
    enrichment_db,
) -> None:
    """The reproduction this test guards against: two near-simultaneous
    profile views (or an agent call racing a profile view) for the SAME
    company must not both call start_or_join_job() and each get a distinct
    run_id -- exactly the shape of the pipeline_runs id 754/755 incident
    find_in_flight_run() was built to prevent for the unrelated
    (step, run_id) key (PR #157) -- generalized here to a per-entity
    (company_id) key instead."""
    engine, company_id = enrichment_db
    with Session(engine) as first_session:
        first_run_id, first_joined = start_or_join_job(
            first_session, company_id, trigger="profile_view"
        )

    with Session(engine) as second_session:
        second_run_id, second_joined = start_or_join_job(
            second_session, company_id, trigger="agent"
        )

    assert first_joined is False
    assert second_joined is True
    assert second_run_id == first_run_id
    assert _job_count(engine, company_id) == 1


def test_a_third_request_after_the_job_finishes_starts_a_fresh_job(
    enrichment_db,
) -> None:
    """Once the in-flight job is no longer 'running', dedup must not
    permanently block that company -- a later request starts a new job."""
    from sqlalchemy import update

    from db.company_enrichment_tables import company_enrichment_jobs

    engine, company_id = enrichment_db
    with Session(engine) as session:
        first_run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")
        session.execute(
            update(company_enrichment_jobs)
            .where(company_enrichment_jobs.c.run_id == first_run_id)
            .values(status="success")
        )
        session.commit()

    with Session(engine) as session:
        second_run_id, joined = start_or_join_job(session, company_id, trigger="manual")

    assert joined is False
    assert second_run_id != first_run_id
    assert _job_count(engine, company_id) == 2


def test_find_active_job_returns_none_when_nothing_is_running(enrichment_db) -> None:
    engine, company_id = enrichment_db
    with Session(engine) as session:
        assert find_active_job(session, company_id) is None


def test_a_dead_worker_with_an_expired_lease_is_reclaimed_not_joined_forever(
    enrichment_db,
) -> None:
    """Pre-PR review finding: without this, a process killed mid-cascade
    (Railway restartPolicyType="ON_FAILURE" -- railway.toml -- is a real,
    recurring event) leaves company_enrichment_jobs status='running'
    forever, and every future request for that company would join the
    same dead run_id forever, never making progress. Reproduces the exact
    scenario: a job is started, its lease is backdated (simulating a dead
    worker whose lease was never heartbeat-renewed and has now expired),
    and a new request must reclaim it (mark it 'failed') and start a
    genuinely fresh job -- not join the dead one."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from db.company_enrichment_tables import company_enrichment_jobs

    engine, company_id = enrichment_db
    with Session(engine) as session:
        dead_run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")
        session.execute(
            update(company_enrichment_jobs)
            .where(company_enrichment_jobs.c.run_id == dead_run_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(hours=2))
        )
        session.commit()

    with Session(engine) as session:
        new_run_id, joined = start_or_join_job(
            session, company_id, trigger="profile_view"
        )

    assert new_run_id != dead_run_id
    assert joined is False  # a genuinely NEW job, not a join

    with engine.connect() as conn:
        statuses = dict(
            conn.execute(
                text(
                    "SELECT run_id, status FROM company_enrichment_jobs WHERE company_id = :id"
                ),
                {"id": company_id},
            ).all()
        )
    assert statuses[dead_run_id] == "failed"  # reclaimed, not left dangling
    assert statuses[new_run_id] == "running"


def test_a_live_job_within_its_lease_is_never_reclaimed(enrichment_db) -> None:
    """The flip side: reclaim_stale_job() must never touch a job whose
    lease has not actually expired -- a genuinely in-progress job must
    still be joined normally, exactly as before this fix."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        live_run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")
        reclaimed = reclaim_stale_job(session, live_run_id)

    assert reclaimed is False

    with Session(engine) as session:
        run_id, joined = start_or_join_job(session, company_id, trigger="agent")

    assert joined is True
    assert run_id == live_run_id


def test_two_truly_concurrent_first_requests_never_create_two_running_jobs(
    enrichment_db,
) -> None:
    """Real-thread concurrency (not sequential simulated ordering): two
    threads, each its own Session, call start_or_join_job() at the same
    instant for a company with NO existing job. Postgres's ON CONFLICT
    DO NOTHING against ux_company_enrichment_jobs_company_active must
    serialize the two INSERTs -- exactly one creates a row, the other
    joins it. Neither may raise, and the company must never end up with
    two 'running' rows."""
    engine, company_id = enrichment_db
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _attempt(key: str) -> None:
        barrier.wait(timeout=5)
        try:
            with Session(engine) as session:
                results[key] = start_or_join_job(
                    session, company_id, trigger="profile_view"
                )
        except Exception as exc:  # pragma: no cover - failure diagnostics
            results[key] = f"error:{exc}"

    threads = [threading.Thread(target=_attempt, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    outcome_a, outcome_b = results["a"], results["b"]
    assert isinstance(outcome_a, tuple) and isinstance(outcome_b, tuple), (
        outcome_a,
        outcome_b,
    )
    run_id_a, joined_a = outcome_a
    run_id_b, joined_b = outcome_b

    assert run_id_a == run_id_b  # both converge on the SAME job
    assert sorted([joined_a, joined_b]) == [
        False,
        True,
    ]  # exactly one starter, one joiner
    assert _job_count(engine, company_id) == 1  # never two running jobs


def test_two_truly_concurrent_reclaim_attempts_produce_exactly_one_fresh_job(
    enrichment_db,
) -> None:
    """Real-thread concurrency for the reclaim path (the pre-PR-review
    finding's fix): two threads race start_or_join_job() against the SAME
    stale (expired-lease) job at the same instant. Exactly one thread's
    reclaim_stale_job() UPDATE must actually flip the row to 'failed'
    (Postgres row-lock serializes the two concurrent UPDATEs against the
    same run_id); the other must see rowcount=0 and NOT raise. Both then
    race a fresh INSERT, which ON CONFLICT DO NOTHING again serializes to
    exactly one winner -- the net result must be exactly one dead
    ('failed') row and exactly one new 'running' row, never two running
    jobs and never a duplicate 'failed' transition error."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from db.company_enrichment_tables import company_enrichment_jobs

    engine, company_id = enrichment_db
    with Session(engine) as session:
        dead_run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")
        session.execute(
            update(company_enrichment_jobs)
            .where(company_enrichment_jobs.c.run_id == dead_run_id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(hours=2))
        )
        session.commit()

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _attempt(key: str) -> None:
        barrier.wait(timeout=5)
        try:
            with Session(engine) as session:
                results[key] = start_or_join_job(
                    session, company_id, trigger="profile_view"
                )
        except Exception as exc:  # pragma: no cover - failure diagnostics
            results[key] = f"error:{exc}"

    threads = [threading.Thread(target=_attempt, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    outcome_a, outcome_b = results["a"], results["b"]
    assert isinstance(outcome_a, tuple) and isinstance(outcome_b, tuple), (
        outcome_a,
        outcome_b,
    )
    run_id_a, joined_a = outcome_a
    run_id_b, joined_b = outcome_b

    assert run_id_a == run_id_b  # both converge on the SAME fresh job
    assert run_id_a != dead_run_id  # neither joined the dead one
    assert sorted([joined_a, joined_b]) == [False, True]

    with engine.connect() as conn:
        statuses = dict(
            conn.execute(
                text(
                    "SELECT run_id, status FROM company_enrichment_jobs WHERE company_id = :id"
                ),
                {"id": company_id},
            ).all()
        )
    assert statuses[dead_run_id] == "failed"  # reclaimed exactly once
    assert statuses[run_id_a] == "running"
    assert len(statuses) == 2  # dead row + exactly one fresh row, never more
    assert _job_count(engine, company_id) == 2


def test_repeated_join_attempts_all_return_the_same_run_id(enrichment_db) -> None:
    """Not just two -- an arbitrary number of concurrent joiners must all
    converge on the one active run_id."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        first_run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    joined_run_ids = set()
    for _ in range(5):
        with Session(engine) as session:
            run_id, joined = start_or_join_job(
                session, company_id, trigger="profile_view"
            )
            assert joined is True
            joined_run_ids.add(run_id)

    assert joined_run_ids == {first_run_id}
    assert _job_count(engine, company_id) == 1
