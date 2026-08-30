"""Local-Postgres regression tests for the PostgreSQL-backed pipeline
coordinator backend (R1, migration 032) -- proves durability across
sessions, database-level single-active-run enforcement, concurrent-call
safety, and stale-run (lease/TTL) recovery. Every test here explicitly
selects PIPELINE_COORDINATOR_BACKEND=postgres (the default is "legacy" --
see tests/unit/test_tender_data_pipeline.py for the default-path ordering
tests, and tests/unit/test_pipeline_coordinator_backend.py for the flag
selection logic itself: unknown value, preflight schema check, legacy DB
isolation).
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from db.pipeline_coordinator_ddl import pipeline_coordinator_migration_statements
from pipeline import run_coordinator as coordinator
from pipeline.run_coordinator import PipelineOrderError, PipelineRunConflictError
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def coordinator_db(monkeypatch: pytest.MonkeyPatch):
    """Real local-Postgres-backed coordinator state, reset before and after
    each test -- see tests/unit/test_tender_data_pipeline.py's fixture of
    the same shape for the rationale. Also selects the postgres backend for
    the duration of the test (the default is "legacy")."""
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


def test_state_survives_a_brand_new_session_and_connection(coordinator_db) -> None:
    """Proves the state isn't process-local memory (the old threading.Lock
    + JSON-file bug this PR fixes): read it back through a completely
    separate engine/connection, not anything cached by db.connection."""
    coordinator.begin_run("restart-run")
    coordinator.begin_tender_scrape("restart-run")
    coordinator.mark_tender_scrape_step("restart-run", "scrape-federal")

    # str(coordinator_db.url) masks the password ('***') by SQLAlchemy's own
    # design (URL.__str__ hides credentials by default) -- pass the URL
    # object directly instead, which create_engine accepts and which never
    # renders the password to a string at all.
    fresh_engine = create_engine(
        coordinator_db.url, connect_args={"connect_timeout": 3}
    )
    try:
        with fresh_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT phase, tender_scrape_started_at FROM pipeline_coordinator_runs "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": "restart-run"},
            ).one()
            step_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM pipeline_coordinator_steps WHERE run_id = :run_id"
                ),
                {"run_id": "restart-run"},
            ).scalar_one()
    finally:
        fresh_engine.dispose()

    assert row.phase == "tender_scrape"
    assert row.tender_scrape_started_at is not None
    assert step_count == 1

    # A brand-new coordinator call (its own fresh session under the hood)
    # sees the same state too.
    state = coordinator.get_run_state()
    assert state is not None
    assert state.run_id == "restart-run"
    assert state.completed_tender_scrapes == ["scrape-federal"]


def test_partial_unique_index_rejects_second_active_row_at_db_level(
    coordinator_db,
) -> None:
    """Bypasses pipeline.run_coordinator's Python-level check entirely --
    proves the "only one active run per scope" guarantee is a real database
    constraint (migration 032's partial unique index), not just an
    application-level convention."""
    with coordinator_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pipeline_coordinator_runs "
                "(run_id, pipeline_scope, status, phase, lease_expires_at) "
                "VALUES (:run_id, 'tender_data', 'active', 'running', NOW() + INTERVAL '1 hour')"
            ),
            {"run_id": "db-level-a"},
        )

    with pytest.raises(IntegrityError):
        with coordinator_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO pipeline_coordinator_runs "
                    "(run_id, pipeline_scope, status, phase, lease_expires_at) "
                    "VALUES (:run_id, 'tender_data', 'active', 'running', "
                    "NOW() + INTERVAL '1 hour')"
                ),
                {"run_id": "db-level-b"},
            )


def test_begin_run_conflict_does_not_replace_active_run(coordinator_db) -> None:
    coordinator.begin_run("conflict-a")
    coordinator.begin_tender_scrape("conflict-a")

    with pytest.raises(PipelineRunConflictError):
        coordinator.begin_run("conflict-b")

    # The active run is still conflict-a, untouched, not silently
    # overwritten by the losing request.
    state = coordinator.get_run_state()
    assert state is not None
    assert state.run_id == "conflict-a"
    assert state.phase == "tender_scrape"


def test_two_concurrent_different_run_ids_only_one_wins(coordinator_db) -> None:
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _start(run_id: str, key: str) -> None:
        barrier.wait(timeout=5)
        try:
            coordinator.begin_run(run_id)
            results[key] = "started"
        except PipelineRunConflictError:
            results[key] = "conflict"
        except Exception as exc:  # pragma: no cover - failure diagnostics
            results[key] = f"error:{exc}"

    threads = [
        threading.Thread(target=_start, args=("thread-run-a", "a")),
        threading.Thread(target=_start, args=("thread-run-b", "b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    outcomes = sorted(results.values())
    assert outcomes == ["conflict", "started"], results

    winner = "thread-run-a" if results["a"] == "started" else "thread-run-b"
    state = coordinator.get_run_state()
    assert state is not None
    assert state.run_id == winner


def test_concurrent_completion_of_same_step_is_idempotent(coordinator_db) -> None:
    coordinator.begin_run("step-race")
    coordinator.begin_tender_scrape("step-race")

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _mark() -> None:
        barrier.wait(timeout=5)
        try:
            coordinator.mark_tender_scrape_step("step-race", "scrape-federal")
        except Exception as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)

    threads = [threading.Thread(target=_mark) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    state = coordinator.get_run_state()
    assert state is not None
    assert state.completed_tender_scrapes == ["scrape-federal"]


def test_stale_active_run_is_reclaimed_after_lease_expiry(coordinator_db) -> None:
    coordinator.begin_run("stale-run")
    with coordinator_db.begin() as conn:
        conn.execute(
            text(
                "UPDATE pipeline_coordinator_runs "
                "SET lease_expires_at = NOW() - INTERVAL '1 hour' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "stale-run"},
        )

    # A new run can now start -- the expired lease releases the scope slot
    # instead of blocking forever.
    new_state = coordinator.begin_run("fresh-after-stale")
    assert new_state.run_id == "fresh-after-stale"

    with coordinator_db.connect() as conn:
        old_row = conn.execute(
            text(
                "SELECT status, stale_reclaimed, success FROM pipeline_coordinator_runs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "stale-run"},
        ).one()
    assert old_row.status == "finished"
    assert old_row.stale_reclaimed is True
    assert old_row.success is False


def test_import_blocked_with_partial_scraper_steps(coordinator_db) -> None:
    coordinator.begin_run("partial-steps")
    coordinator.begin_tender_scrape("partial-steps")
    coordinator.mark_tender_scrape_step("partial-steps", "scrape-federal")
    coordinator.mark_tender_scrape_step("partial-steps", "scrape-merx-arch")

    with pytest.raises(PipelineOrderError, match="scrape-commercial"):
        coordinator.assert_ready_for_import("partial-steps")


def test_begin_run_resumes_same_run_id_idempotently(coordinator_db) -> None:
    coordinator.begin_run("resume-run")
    coordinator.begin_tender_scrape("resume-run")
    coordinator.mark_tender_scrape_step("resume-run", "scrape-federal")

    # Calling begin_run again with the SAME run_id must not reset progress.
    resumed = coordinator.begin_run("resume-run")
    assert resumed.run_id == "resume-run"
    assert resumed.completed_tender_scrapes == ["scrape-federal"]


def test_begin_or_resume_run_without_run_id_reuses_active_run(coordinator_db) -> None:
    started = coordinator.begin_run("explicit-run")
    reused = coordinator.begin_or_resume_run()
    assert reused.run_id == started.run_id

    another = coordinator.begin_or_resume_run(None)
    assert another.run_id == started.run_id


def test_late_worker_cannot_mutate_finished_run(coordinator_db) -> None:
    coordinator.begin_run("late-worker-finished")
    coordinator.begin_tender_scrape("late-worker-finished")
    coordinator.finish_run("late-worker-finished", success=True)

    with pytest.raises(PipelineOrderError, match="no longer active"):
        coordinator.mark_tender_scrape_step("late-worker-finished", "scrape-federal")

    # Nothing was mutated by the refused call.
    with coordinator_db.connect() as conn:
        step_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM pipeline_coordinator_steps WHERE run_id = :run_id"
            ),
            {"run_id": "late-worker-finished"},
        ).scalar_one()
    assert step_count == 0


def test_late_worker_cannot_revive_expired_stale_run(coordinator_db) -> None:
    coordinator.begin_run("late-worker-stale")
    coordinator.begin_tender_scrape("late-worker-stale")
    with coordinator_db.begin() as conn:
        conn.execute(
            text(
                "UPDATE pipeline_coordinator_runs "
                "SET lease_expires_at = NOW() - INTERVAL '1 hour' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "late-worker-stale"},
        )

    # Nobody else has reclaimed it yet -- this late call must self-detect
    # the expired lease, reclaim under its own lock, and refuse to apply
    # its own requested mutation.
    with pytest.raises(PipelineOrderError, match="lease expired"):
        coordinator.mark_tender_scrape_step("late-worker-stale", "scrape-federal")

    with coordinator_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stale_reclaimed FROM pipeline_coordinator_runs "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "late-worker-stale"},
        ).one()
        step_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM pipeline_coordinator_steps WHERE run_id = :run_id"
            ),
            {"run_id": "late-worker-stale"},
        ).scalar_one()
    assert row.status == "finished"
    assert row.stale_reclaimed is True
    assert step_count == 0


def test_begin_import_enforces_readiness_without_prior_assert(coordinator_db) -> None:
    """begin_import() must be a self-sufficient enforcement point -- calling
    it directly, without ever calling assert_ready_for_import() first, must
    still block until every required scraper step is complete."""
    coordinator.begin_run("direct-import-block")
    coordinator.begin_tender_scrape("direct-import-block")
    coordinator.mark_tender_scrape_step("direct-import-block", "scrape-federal")
    # Deliberately skip the remaining steps and skip assert_ready_for_import.

    with pytest.raises(PipelineOrderError, match="Import blocked"):
        coordinator.begin_import("direct-import-block")

    state = coordinator.get_run_state()
    assert state is not None
    assert state.import_started_at is None


def test_late_old_worker_does_not_affect_new_active_run_after_reclaim(
    coordinator_db,
) -> None:
    coordinator.begin_run("old-run")
    coordinator.begin_tender_scrape("old-run")
    with coordinator_db.begin() as conn:
        conn.execute(
            text(
                "UPDATE pipeline_coordinator_runs "
                "SET lease_expires_at = NOW() - INTERVAL '1 hour' "
                "WHERE run_id = :run_id"
            ),
            {"run_id": "old-run"},
        )

    # A new run starts, reclaiming the stale old one as part of begin_run.
    new_state = coordinator.begin_run("new-run")
    assert new_state.run_id == "new-run"
    coordinator.begin_tender_scrape("new-run")
    coordinator.mark_tender_scrape_step("new-run", "scrape-federal")

    # The old worker, unaware anything happened, tries to keep going.
    with pytest.raises(PipelineOrderError):
        coordinator.mark_tender_scrape_step("old-run", "scrape-merx-arch")

    # The new run's state is completely untouched by the old worker's
    # refused attempt.
    state = coordinator.get_run_state()
    assert state is not None
    assert state.run_id == "new-run"
    assert state.completed_tender_scrapes == ["scrape-federal"]
