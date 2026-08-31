"""Real local-Postgres regression tests for pipeline.runs.find_in_flight_run()
-- the query pipeline.runs.py gained to let api.internal._enqueue_step()
detect a retried/duplicate HTTP trigger for the same (step, run_id) before
inserting a second start_run() row and scheduling a second concurrent
background worker.

Reproduces, at the query level, the exact production incident that
motivated this: pipeline_runs id 754 and 755 (step="import-csvs",
run_id="n8n-5564", started ~90 seconds apart on 2026-08-05) -- both
inserted independently by start_run(), both left status="running"
forever, because nothing before this ever checked "is a run for this
(step, run_id) already in flight" before creating another one.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pipeline.runs import find_in_flight_run, start_run
from tests.db_test_safety import require_local_test_database

_TEST_RUN_PREFIX = "test-dedup-"


def _cleanup(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM pipeline_runs WHERE run_id LIKE :prefix"),
            {"prefix": f"{_TEST_RUN_PREFIX}%"},
        )


def _session_for(engine) -> Session:
    return Session(bind=engine)


def test_no_row_at_all_returns_none():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    _cleanup(engine)
    try:
        session = _session_for(engine)
        try:
            result = find_in_flight_run(
                session, "import-csvs", f"{_TEST_RUN_PREFIX}no-row"
            )
        finally:
            session.close()
        assert result is None
    finally:
        _cleanup(engine)
        engine.dispose()


def test_running_row_for_same_step_and_run_id_is_found():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    _cleanup(engine)
    try:
        run_id = f"{_TEST_RUN_PREFIX}running"
        session = _session_for(engine)
        try:
            created = start_run(session, "import-csvs", run_id)
            found = find_in_flight_run(session, "import-csvs", run_id)
        finally:
            session.close()
        assert found is not None
        assert found.id == created.id
        assert found.status == "running"
    finally:
        _cleanup(engine)
        engine.dispose()


def test_finished_row_is_not_treated_as_in_flight():
    """A genuine re-run of the same run_id after the prior attempt already
    finished (success or failure) must NOT be blocked -- only a still-
    "running" row counts as in-flight."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    _cleanup(engine)
    try:
        run_id = f"{_TEST_RUN_PREFIX}finished"
        session = _session_for(engine)
        try:
            created = start_run(session, "import-csvs", run_id)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE pipeline_runs SET status = 'success', "
                        "finished_at = NOW() WHERE id = :id"
                    ),
                    {"id": created.id},
                )
            found = find_in_flight_run(session, "import-csvs", run_id)
        finally:
            session.close()
        assert found is None
    finally:
        _cleanup(engine)
        engine.dispose()


def test_different_step_with_same_run_id_is_not_a_match():
    """The same run_id used across two different steps (e.g. a shared
    n8n-orchestration run_id spanning several endpoints) must not cross-
    match -- in-flight detection is scoped to (step, run_id) together."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    _cleanup(engine)
    try:
        run_id = f"{_TEST_RUN_PREFIX}cross-step"
        session = _session_for(engine)
        try:
            start_run(session, "scrape-federal", run_id)
            found = find_in_flight_run(session, "import-csvs", run_id)
        finally:
            session.close()
        assert found is None
    finally:
        _cleanup(engine)
        engine.dispose()


def test_two_preexisting_running_rows_returns_the_most_recent_without_error():
    """If duplicate running rows already exist for this (step, run_id) --
    e.g. left over from before this check existed -- the query is a
    best-effort lookup (no unique constraint backs it): it returns the
    most recent one rather than raising."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    _cleanup(engine)
    try:
        run_id = f"{_TEST_RUN_PREFIX}two-running"
        session = _session_for(engine)
        try:
            start_run(session, "import-csvs", run_id)
            newer = start_run(session, "import-csvs", run_id)
            found = find_in_flight_run(session, "import-csvs", run_id)
        finally:
            session.close()
        assert found is not None
        assert found.id == newer.id
    finally:
        _cleanup(engine)
        engine.dispose()
