"""Unit tests for deterministic tender data pipeline ordering (P1-01).

Exercises pipeline.run_coordinator with PIPELINE_COORDINATOR_BACKEND left
at its default ("legacy") -- these tests must pass without any database,
proving the default/legacy path is unchanged and untouched by R1. The
Postgres-backed backend has its own test suite:
tests/unit/test_pipeline_coordinator_db.py (ordering/concurrency/stale-run,
with PIPELINE_COORDINATOR_BACKEND=postgres explicitly set) and
tests/unit/test_pipeline_coordinator_backend.py (backend selection itself:
unknown flag, legacy DB isolation, postgres preflight schema check).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline import run_coordinator as coordinator
from pipeline import run_coordinator_legacy
from pipeline.run_coordinator import PipelineOrderError, assert_import_not_before_scrape
from pipeline.tender_data_pipeline import run_tender_data_pipeline


@pytest.fixture
def coordinator_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_path = tmp_path / "run_coordinator.json"
    monkeypatch.setattr(run_coordinator_legacy, "_STATE_PATH", state_path)
    return state_path


def test_import_blocked_before_tender_scrapes(coordinator_state: Path) -> None:
    coordinator.begin_run("run-1")
    coordinator.begin_tender_scrape("run-1")

    with pytest.raises(PipelineOrderError, match="Import blocked"):
        coordinator.assert_ready_for_import("run-1")


def test_import_allowed_after_all_tender_scrapes(coordinator_state: Path) -> None:
    coordinator.begin_run("run-2")
    coordinator.begin_tender_scrape("run-2")
    for step in coordinator.TENDER_SCRAPE_STEPS:
        coordinator.mark_tender_scrape_step("run-2", step)

    coordinator.assert_ready_for_import("run-2")
    coordinator.begin_import("run-2")

    audit = assert_import_not_before_scrape()
    assert audit["ordering_ok"] == "True"
    assert audit["tender_scrape_finished_at"] <= audit["import_started_at"]


def test_mark_last_scrape_step_sets_finished_timestamp(coordinator_state: Path) -> None:
    coordinator.begin_run("run-3")
    coordinator.begin_tender_scrape("run-3")
    for step in coordinator.TENDER_SCRAPE_STEPS[:-1]:
        coordinator.mark_tender_scrape_step("run-3", step)

    state = coordinator.get_run_state()
    assert state is not None
    assert state.tender_scrape_finished_at is None

    coordinator.mark_tender_scrape_step("run-3", coordinator.TENDER_SCRAPE_STEPS[-1])
    state = coordinator.get_run_state()
    assert state is not None
    assert state.tender_scrape_finished_at is not None


def test_tender_data_pipeline_runs_phases_in_order(coordinator_state: Path) -> None:
    phase_log: list[str] = []
    auxiliary_triggers: list[str] = []

    def _fake_tender_scrapers(run_id: str) -> dict:
        phase_log.append("tender_scrape")
        coordinator.begin_tender_scrape(run_id)
        for step in coordinator.TENDER_SCRAPE_STEPS:
            coordinator.mark_tender_scrape_step(run_id, step)
        coordinator.complete_tender_scrape(run_id)
        return {
            "scrape_started_at": "2026-07-01T10:00:00+00:00",
            "scrape_finished_at": "2026-07-01T10:05:00+00:00",
            "steps": {},
        }

    def _fake_auxiliary(*, trigger: str) -> dict:
        phase_log.append("auxiliary_scrape")
        auxiliary_triggers.append(trigger)
        return {}

    def _fake_csv_verify(**kwargs) -> dict:
        phase_log.append("csv_verify")
        return {"federal_merx_tenders": 10}

    session = MagicMock()

    with patch(
        "pipeline.tender_data_pipeline.run_tender_scrapers",
        side_effect=_fake_tender_scrapers,
    ):
        with patch(
            "pipeline.tender_data_pipeline.run_auxiliary_scrapers",
            side_effect=_fake_auxiliary,
        ):
            with patch(
                "pipeline.tender_data_pipeline.verify_tender_csvs",
                side_effect=_fake_csv_verify,
            ):
                with patch("pipeline.tender_data_pipeline.init_db"):
                    with patch(
                        "pipeline.tender_data_pipeline.get_session",
                        return_value=session,
                    ):
                        with patch(
                            "pipeline.tender_data_pipeline.count_table_rows",
                            return_value={
                                "tenders": 5,
                                "commercial_tenders": 2,
                                "arch_tenders": 1,
                            },
                        ):
                            with patch(
                                "pipeline.tender_data_pipeline.import_all_csvs",
                                return_value={
                                    "tenders": 10,
                                    "commercial_tenders": 2,
                                    "arch_tenders": 1,
                                },
                            ) as import_all:
                                with patch(
                                    "pipeline.tender_data_pipeline.import_contract_awards",
                                    return_value=0,
                                ):
                                    with patch(
                                        "pipeline.tender_data_pipeline.refresh_company_award_stats"
                                    ):
                                        with patch(
                                            "pipeline.tender_data_pipeline.verify_database_counts",
                                            return_value={"tenders": 10},
                                        ):
                                            summary = run_tender_data_pipeline(
                                                run_id="audit-run"
                                            )

    assert phase_log == [
        "tender_scrape",
        "auxiliary_scrape",
        "csv_verify",
    ]
    assert summary["status"] == "success"
    import_all.assert_called_once()
    # Scheduled/default: no explicit trigger kwarg was passed to
    # run_tender_data_pipeline() above, so run_auxiliary_scrapers() must
    # receive the honest default, "scheduler".
    assert auxiliary_triggers == ["scheduler"]

    state = json.loads(coordinator_state.read_text(encoding="utf-8"))
    assert state["tender_scrape_finished_at"] is not None
    assert state["import_started_at"] is not None
    assert state["import_finished_at"] is not None
    assert state["import_started_at"] >= state["tender_scrape_finished_at"]


def _run_with_stubbed_phases(*, trigger, run_id, coordinator_state, capture):
    """Shared scaffolding for the trigger-propagation tests below --
    stubs every downstream phase (tender scrape, auxiliary scrapers, CSV
    verify, DB import/verify) so only run_tender_data_pipeline()'s own
    trigger validation/propagation logic is under test. `capture` is a
    dict this helper fills with `auxiliary_triggers` (the trigger value(s)
    run_auxiliary_scrapers() was actually called with)."""
    auxiliary_triggers: list[str] = []
    capture["auxiliary_triggers"] = auxiliary_triggers

    def _fake_tender_scrapers(run_id: str) -> dict:
        coordinator.begin_tender_scrape(run_id)
        for step in coordinator.TENDER_SCRAPE_STEPS:
            coordinator.mark_tender_scrape_step(run_id, step)
        coordinator.complete_tender_scrape(run_id)
        return {
            "scrape_started_at": "2026-07-01T10:00:00+00:00",
            "scrape_finished_at": "2026-07-01T10:05:00+00:00",
            "steps": {},
        }

    def _fake_auxiliary(*, trigger: str) -> dict:
        auxiliary_triggers.append(trigger)
        return {}

    session = MagicMock()

    with patch(
        "pipeline.tender_data_pipeline.run_tender_scrapers",
        side_effect=_fake_tender_scrapers,
    ):
        with patch(
            "pipeline.tender_data_pipeline.run_auxiliary_scrapers",
            side_effect=_fake_auxiliary,
        ):
            with patch(
                "pipeline.tender_data_pipeline.verify_tender_csvs",
                return_value={"federal_merx_tenders": 10},
            ):
                with patch("pipeline.tender_data_pipeline.init_db"):
                    with patch(
                        "pipeline.tender_data_pipeline.get_session",
                        return_value=session,
                    ):
                        with patch(
                            "pipeline.tender_data_pipeline.count_table_rows",
                            return_value={"tenders": 5},
                        ):
                            with patch(
                                "pipeline.tender_data_pipeline.import_all_csvs",
                                return_value={"tenders": 10},
                            ):
                                with patch(
                                    "pipeline.tender_data_pipeline.import_contract_awards",
                                    return_value=0,
                                ):
                                    with patch(
                                        "pipeline.tender_data_pipeline.refresh_company_award_stats"
                                    ):
                                        with patch(
                                            "pipeline.tender_data_pipeline.verify_database_counts",
                                            return_value={"tenders": 10},
                                        ):
                                            kwargs = {"run_id": run_id}
                                            if trigger is not None:
                                                kwargs["trigger"] = trigger
                                            return run_tender_data_pipeline(**kwargs)


def test_scheduled_default_trigger_propagates_to_auxiliary_scrapers(
    coordinator_state: Path,
) -> None:
    """Backward compatibility: a caller passing no trigger at all (the
    exact shape pipeline/run.py's scheduled call uses) must still work
    unchanged and must propagate the honest default, "scheduler"."""
    capture: dict = {}
    summary = _run_with_stubbed_phases(
        trigger=None,
        run_id="sched-run",
        coordinator_state=coordinator_state,
        capture=capture,
    )

    assert summary["status"] == "success"
    assert capture["auxiliary_triggers"] == ["scheduler"]


def test_manual_trigger_propagates_to_auxiliary_scrapers(
    coordinator_state: Path,
) -> None:
    capture: dict = {}
    summary = _run_with_stubbed_phases(
        trigger="manual",
        run_id="manual-run",
        coordinator_state=coordinator_state,
        capture=capture,
    )

    assert summary["status"] == "success"
    assert capture["auxiliary_triggers"] == ["manual"]


def test_invalid_trigger_raises_before_any_coordinator_mutation(
    coordinator_state: Path,
) -> None:
    begin_run_mock = MagicMock()
    begin_full_scrape_mock = MagicMock()

    with patch("pipeline.tender_data_pipeline.begin_run", begin_run_mock):
        with patch(
            "pipeline.tender_data_pipeline.begin_full_scrape", begin_full_scrape_mock
        ):
            with pytest.raises(ValueError, match="trigger must be one of"):
                run_tender_data_pipeline(run_id="bad-run", trigger="bogus")

    begin_run_mock.assert_not_called()
    begin_full_scrape_mock.assert_not_called()


def test_internal_import_rejected_before_scrape() -> None:
    from fastapi import HTTPException

    from api import internal as internal_api

    background_tasks = MagicMock()
    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch(
            "api.internal.assert_import_allowed",
            side_effect=PipelineOrderError("blocked"),
        ):
            with pytest.raises(HTTPException) as exc:
                internal_api.import_csvs(background_tasks, None)
    assert exc.value.status_code == 409


def test_internal_import_without_body_uses_active_run(coordinator_state: Path) -> None:
    from api import internal as internal_api

    coordinator.begin_run("active-run")
    coordinator.begin_tender_scrape("active-run")
    for step in coordinator.TENDER_SCRAPE_STEPS:
        coordinator.mark_tender_scrape_step("active-run", step)

    background_tasks = MagicMock()
    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch(
            "api.internal._enqueue_step", return_value={"status": "started"}
        ) as enqueue_step:
            response = internal_api.import_csvs(background_tasks, None)

    assert response == {"status": "started"}
    enqueue_step.assert_called_once()
    assert enqueue_step.call_args.args[3] == "active-run"


def test_internal_import_sync_without_body_uses_active_run(
    coordinator_state: Path,
) -> None:
    from api import internal as internal_api

    coordinator.begin_run("sync-run")
    coordinator.begin_tender_scrape("sync-run")
    for step in coordinator.TENDER_SCRAPE_STEPS:
        coordinator.mark_tender_scrape_step("sync-run", step)

    background_tasks = MagicMock()
    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch(
            "api.internal._run_step_sync", return_value={"status": "success"}
        ) as run_step_sync:
            response = internal_api.import_csvs(background_tasks, None, sync=True)

    assert response == {"status": "success"}
    run_step_sync.assert_called_once()
    assert run_step_sync.call_args.args[0] == "import-csvs"
    assert run_step_sync.call_args.args[2] == "sync-run"


def test_manual_full_pipeline_endpoint_passes_trigger_manual(
    coordinator_state: Path,
) -> None:
    """(M3F foundation) POST /internal/pipeline/tender-data must call
    run_tender_data_pipeline() with trigger="manual" explicitly -- this
    is the one real production caller, besides the scheduled cron, that
    honestly is NOT "scheduler". coordinator_state isolates the ordering
    audit's own coordinator-state read (assert_import_not_before_scrape())
    into a tmp_path, the same isolation every other coordinator-touching
    test in this file already uses -- not itself under test here."""
    from api import internal as internal_api

    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch(
            "api.internal.run_tender_data_pipeline",
            return_value={"status": "success", "run_id": "manual-endpoint-run"},
        ) as run_tender_data_pipeline_mock:
            response = internal_api.run_tender_data_pipeline_route(None, sync=True)

    assert response["status"] == "success"
    run_tender_data_pipeline_mock.assert_called_once_with(run_id=None, trigger="manual")
