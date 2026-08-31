"""Local-Postgres regression tests for the standalone tender_data
coordinator stale-run reaper (pipeline.run_coordinator_postgres.
reap_stale_run() / describe_active_run()).

Distinct from tests/unit/test_pipeline_coordinator_db.py's
test_stale_active_run_is_reclaimed_after_lease_expiry and
test_late_worker_cannot_revive_expired_stale_run: those prove reclaim
happens as a *side effect* of begin_run()/a late worker callback. This
file proves the *standalone* action -- reap_stale_run() -- works
correctly when called directly, with no other coordinator call involved:
it must distinguish a real active lease from an expired one, never touch
a live run, be safe to call concurrently and repeatedly, and leave the
same audit trail (stale_reclaimed/success/error) the existing reclaim
paths already leave.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, text

from db.pipeline_coordinator_ddl import pipeline_coordinator_migration_statements
from pipeline import run_coordinator as coordinator
from pipeline.run_coordinator_postgres import describe_active_run, reap_stale_run
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def coordinator_db(monkeypatch: pytest.MonkeyPatch):
    """Real local-Postgres-backed coordinator state, reset before and after
    each test -- same shape as tests/unit/test_pipeline_coordinator_db.py's
    fixture. Also selects the postgres backend for the duration of the
    test (the default is "legacy")."""
    monkeypatch.setenv("PIPELINE_COORDINATOR_BACKEND", "postgres")
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in pipeline_coordinator_migration_statements():
            conn.execute(text(statement))

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM pipeline_coordinator_steps"))
            conn.execute(text("DELETE FROM pipeline_coordinator_runs"))

    _reset()
    try:
        yield engine
    finally:
        _reset()
        engine.dispose()


def _expire_lease(engine, run_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE pipeline_coordinator_runs "
                "SET lease_expires_at = NOW() - INTERVAL '1 hour' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        )


def test_no_active_run_is_a_clean_noop(coordinator_db) -> None:
    """Nothing has ever started a run for this scope: reap must report
    no_active_run, not raise, not fabricate a row."""
    assert describe_active_run() is None

    result = reap_stale_run()
    assert result == {"reclaimed": False, "reason": "no_active_run", "run_id": None}


def test_active_lease_is_never_reclaimed(coordinator_db) -> None:
    """A run that is genuinely still alive (lease not yet expired) must be
    left completely untouched -- this is the core 'don't reclaim a live
    process' guarantee."""
    coordinator.begin_run("live-run")
    coordinator.begin_tender_scrape("live-run")

    described = describe_active_run()
    assert described is not None
    assert described["run_id"] == "live-run"
    assert described["lease_expired"] is False

    result = reap_stale_run()
    assert result["reclaimed"] is False
    assert result["reason"] == "lease_still_valid"
    assert result["run_id"] == "live-run"

    # Confirm the row is truly untouched.
    with coordinator_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stale_reclaimed, phase FROM pipeline_coordinator_runs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "live-run"},
        ).one()
    assert row.status == "active"
    assert row.stale_reclaimed is False
    assert row.phase == "tender_scrape"

    # A real, still-active run can proceed normally afterwards.
    coordinator.mark_tender_scrape_step("live-run", "scrape-federal")
    state = coordinator.get_run_state()
    assert state is not None
    assert state.completed_tender_scrapes == ["scrape-federal"]


def test_expired_lease_is_reclaimed_with_audit_trail(coordinator_db) -> None:
    """The core positive case: an expired-lease run is reclaimed, leaves a
    clear audit trail distinguishing it from the begin_run()/late-worker
    reclaim paths, and does so without any other coordinator call."""
    coordinator.begin_run("expired-run")
    coordinator.begin_tender_scrape("expired-run")
    _expire_lease(coordinator_db, "expired-run")

    described = describe_active_run()
    assert described is not None
    assert described["lease_expired"] is True

    result = reap_stale_run()
    assert result["reclaimed"] is True
    assert result["reason"] == "lease_expired"
    assert result["run_id"] == "expired-run"

    with coordinator_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stale_reclaimed, success, phase, finished_at, error "
                "FROM pipeline_coordinator_runs WHERE run_id = :run_id"
            ),
            {"run_id": "expired-run"},
        ).one()
    assert row.status == "finished"
    assert row.stale_reclaimed is True
    assert row.success is False
    assert row.phase == "finished"
    assert row.finished_at is not None
    assert "stale_run_reaper" in row.error

    # The scope slot is free again afterwards -- a new run can start.
    new_state = coordinator.begin_run("after-reap")
    assert new_state.run_id == "after-reap"


def test_orphaned_running_row_with_zero_progress_is_reclaimed(coordinator_db) -> None:
    """A run inserted (begin_run only, no begin_tender_scrape/steps at all)
    that then goes silent and expires must be reclaimed exactly like a
    run that made partial progress -- reclaim must not depend on any
    phase/step having been recorded."""
    coordinator.begin_run("orphaned-at-birth")
    _expire_lease(coordinator_db, "orphaned-at-birth")

    with coordinator_db.connect() as conn:
        before = conn.execute(
            text(
                "SELECT phase, tender_scrape_started_at FROM pipeline_coordinator_runs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "orphaned-at-birth"},
        ).one()
    assert before.phase == "running"
    assert before.tender_scrape_started_at is None

    result = reap_stale_run()
    assert result["reclaimed"] is True
    assert result["run_id"] == "orphaned-at-birth"

    with coordinator_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stale_reclaimed FROM pipeline_coordinator_runs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "orphaned-at-birth"},
        ).one()
    assert row.status == "finished"
    assert row.stale_reclaimed is True


def test_concurrent_reap_calls_reclaim_exactly_once(coordinator_db) -> None:
    """Two threads calling reap_stale_run() at the same instant on the same
    expired run must never both report reclaimed=True, and neither may
    raise -- exactly one reclaims, the other observes no_active_run."""
    coordinator.begin_run("concurrent-stale")
    coordinator.begin_tender_scrape("concurrent-stale")
    _expire_lease(coordinator_db, "concurrent-stale")

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _reap(key: str) -> None:
        barrier.wait(timeout=5)
        try:
            results[key] = reap_stale_run()
        except Exception as exc:  # pragma: no cover - failure diagnostics
            results[key] = f"error:{exc}"

    threads = [
        threading.Thread(target=_reap, args=("a",)),
        threading.Thread(target=_reap, args=("b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    outcomes = [results["a"], results["b"]]
    for outcome in outcomes:
        assert isinstance(outcome, dict), outcomes

    reclaimed_flags = sorted(o["reclaimed"] for o in outcomes)  # type: ignore[index]
    assert reclaimed_flags == [False, True], outcomes

    winner = outcomes[0] if outcomes[0]["reclaimed"] else outcomes[1]  # type: ignore[index]
    loser = outcomes[1] if outcomes[0]["reclaimed"] else outcomes[0]  # type: ignore[index]
    assert winner["run_id"] == "concurrent-stale"  # type: ignore[index]
    assert loser["reason"] == "no_active_run"  # type: ignore[index]

    with coordinator_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stale_reclaimed FROM pipeline_coordinator_runs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "concurrent-stale"},
        ).one()
    assert row.status == "finished"
    assert row.stale_reclaimed is True


def test_repeated_reap_after_reclaim_is_idempotent(coordinator_db) -> None:
    """Calling reap_stale_run() again after it already reclaimed a run
    (sequential, not concurrent) must be a clean no-op -- never a second
    reclaim, never an error."""
    coordinator.begin_run("repeat-reap")
    _expire_lease(coordinator_db, "repeat-reap")

    first = reap_stale_run()
    assert first["reclaimed"] is True
    assert first["run_id"] == "repeat-reap"

    second = reap_stale_run()
    assert second == {"reclaimed": False, "reason": "no_active_run", "run_id": None}

    third = reap_stale_run()
    assert third == second

    with coordinator_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stale_reclaimed, success, error "
                "FROM pipeline_coordinator_runs WHERE run_id = :run_id"
            ),
            {"run_id": "repeat-reap"},
        ).one()
    assert row.status == "finished"
    assert row.stale_reclaimed is True
    assert row.success is False
    # Only one reclaim ever happened -- the error text is not doubled/reset.
    assert row.error.count("stale_run_reaper") == 1
