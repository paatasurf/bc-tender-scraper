"""Unit tests for deterministic tender data pipeline ordering (P1-01)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline import run_coordinator as coordinator
from pipeline.run_coordinator import PipelineOrderError, assert_import_not_before_scrape
from pipeline.tender_data_pipeline import run_tender_data_pipeline


@pytest.fixture
def coordinator_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_path = tmp_path / "run_coordinator.json"
    monkeypatch.setattr(coordinator, "_STATE_PATH", state_path)
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
    scrape_started = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    scrape_finished = datetime(2026, 7, 1, 10, 5, 0, tzinfo=timezone.utc)
    import_started = datetime(2026, 7, 1, 10, 5, 1, tzinfo=timezone.utc)
    import_finished = datetime(2026, 7, 1, 10, 6, 0, tzinfo=timezone.utc)

    def _fake_tender_scrapers(run_id: str) -> dict:
        phase_log.append("tender_scrape")
        coordinator.begin_tender_scrape(run_id)
        for step in coordinator.TENDER_SCRAPE_STEPS:
            coordinator.mark_tender_scrape_step(run_id, step)
        coordinator.complete_tender_scrape(run_id)
        return {
            "scrape_started_at": scrape_started.isoformat(),
            "scrape_finished_at": scrape_finished.isoformat(),
            "steps": {},
        }

    def _fake_auxiliary() -> dict:
        phase_log.append("auxiliary_scrape")
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

    state = json.loads(coordinator_state.read_text(encoding="utf-8"))
    assert state["tender_scrape_finished_at"] is not None
    assert state["import_started_at"] is not None
    assert state["import_finished_at"] is not None
    assert state["import_started_at"] >= state["tender_scrape_finished_at"]


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


def test_internal_tender_scrape_initializes_coordinator_before_worker(
    coordinator_state: Path,
) -> None:
    from api import internal as internal_api

    background_tasks = MagicMock()
    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch(
            "api.internal._enqueue_step",
            return_value={"status": "started", "run_id": "ignored"},
        ) as enqueue_step:
            response = internal_api.scrape_federal(background_tasks, None)

    enqueue_step.assert_called_once()
    actual_run_id = enqueue_step.call_args.args[3]
    assert response == {"status": "started", "run_id": "ignored"}
    state = coordinator.get_run_state()
    assert state is not None
    assert state.run_id == actual_run_id
    assert state.phase == "tender_scrape"
    assert state.tender_scrape_started_at is not None


def test_internal_tender_scrape_without_body_reuses_active_run(
    coordinator_state: Path,
) -> None:
    from api import internal as internal_api

    coordinator.begin_run("active-scrape-run")
    coordinator.begin_tender_scrape("active-scrape-run")
    coordinator.mark_tender_scrape_step("active-scrape-run", "scrape-federal")

    background_tasks = MagicMock()
    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch(
            "api.internal._enqueue_step",
            return_value={"status": "started"},
        ) as enqueue_step:
            response = internal_api.scrape_merx_arch(background_tasks, None)

    assert response == {"status": "started"}
    enqueue_step.assert_called_once()
    assert enqueue_step.call_args.args[3] == "active-scrape-run"
    state = coordinator.get_run_state()
    assert state is not None
    assert state.run_id == "active-scrape-run"
    assert state.completed_tender_scrapes == ["scrape-federal"]
