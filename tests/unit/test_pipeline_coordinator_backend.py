"""Tests for the PIPELINE_COORDINATOR_BACKEND cutover flag itself
(pipeline/run_coordinator.py's dispatcher): default value, fail-closed
rejection of unknown values, the legacy backend never touching the
database, and the postgres backend's preflight schema check (fails clearly
before any mutation when migration 032 hasn't been applied).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pipeline import run_coordinator as coordinator
from pipeline import run_coordinator_legacy
from pipeline.run_coordinator import (
    PipelineCoordinatorBackendError,
    PipelineCoordinatorSchemaNotReadyError,
)
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def legacy_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_path = tmp_path / "run_coordinator.json"
    monkeypatch.setattr(run_coordinator_legacy, "_STATE_PATH", state_path)
    return state_path


def test_unset_backend_env_defaults_to_legacy(legacy_state: Path, monkeypatch) -> None:
    monkeypatch.delenv("PIPELINE_COORDINATOR_BACKEND", raising=False)
    state = coordinator.begin_run("default-backend-run")
    assert state.run_id == "default-backend-run"
    # Only the legacy backend ever writes this file.
    assert legacy_state.exists()


def test_explicit_legacy_backend_behaves_like_default(
    legacy_state: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPELINE_COORDINATOR_BACKEND", "legacy")
    coordinator.begin_run("explicit-legacy-run")
    assert legacy_state.exists()


def test_unknown_backend_value_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("PIPELINE_COORDINATOR_BACKEND", "bogus-value")
    with pytest.raises(PipelineCoordinatorBackendError, match="bogus-value"):
        coordinator.begin_run("should-never-start")


def test_unknown_backend_value_rejected_on_every_call_shape(monkeypatch) -> None:
    """Fail-closed applies to read-only calls too, not just mutating ones."""
    monkeypatch.setenv("PIPELINE_COORDINATOR_BACKEND", "PRODUCTION")  # typo'd value
    with pytest.raises(PipelineCoordinatorBackendError):
        coordinator.get_run_state()


def test_legacy_backend_never_opens_a_database_session(
    legacy_state: Path, monkeypatch
) -> None:
    """Proves the legacy backend has zero database access -- patch
    db.connection.get_session to blow up if anything ever calls it, then
    run a full legacy coordinator sequence and confirm nothing did."""
    monkeypatch.delenv("PIPELINE_COORDINATOR_BACKEND", raising=False)

    def _forbidden_get_session():
        raise AssertionError(
            "legacy backend must never call db.connection.get_session()"
        )

    monkeypatch.setattr("db.connection.get_session", _forbidden_get_session)

    coordinator.begin_run("legacy-no-db-run")
    coordinator.begin_tender_scrape("legacy-no-db-run")
    for step in coordinator.TENDER_SCRAPE_STEPS:
        coordinator.mark_tender_scrape_step("legacy-no-db-run", step)
    coordinator.complete_tender_scrape("legacy-no-db-run")
    coordinator.assert_ready_for_import("legacy-no-db-run")
    coordinator.begin_import("legacy-no-db-run")
    coordinator.complete_import("legacy-no-db-run")
    coordinator.finish_run("legacy-no-db-run", success=True)
    assert coordinator.get_run_state() is not None


def test_postgres_backend_without_migration_raises_clean_preflight_error(
    monkeypatch,
) -> None:
    """If migration 032 hasn't been applied yet, the postgres backend must
    fail with a clear, actionable error before any lock or write -- never
    a raw "relation does not exist", never a silent no-op, never a
    fallback to legacy."""
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS pipeline_coordinator_steps"))
        conn.execute(text("DROP TABLE IF EXISTS pipeline_coordinator_runs"))

    monkeypatch.setenv("PIPELINE_COORDINATOR_BACKEND", "postgres")
    try:
        with pytest.raises(
            PipelineCoordinatorSchemaNotReadyError, match="migration 032"
        ):
            coordinator.begin_run("no-schema-run")

        # No partial state anywhere -- the preflight check ran before any
        # table access, so there is nothing to roll back or clean up.
        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT to_regclass('public.pipeline_coordinator_runs') IS NOT NULL"
                )
            ).scalar_one()
        assert exists is False
    finally:
        engine.dispose()
