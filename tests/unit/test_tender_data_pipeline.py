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
from pipeline import tender_data_pipeline as tender_data_pipeline_module
from pipeline.run_coordinator import PipelineOrderError, assert_import_not_before_scrape
from pipeline.tender_data_pipeline import (
    run_auxiliary_scrapers,
    run_tender_data_pipeline,
    run_tender_scrapers,
)


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


# --- Stage 1: run_tender_scrapers() structured per-source result ----------
#
# (Resilience design, Stage 1 only) run_tender_scrapers() no longer raises
# on a scraper-level failure -- it returns a structured per-source result
# instead. Stage 1 does not change what run_tender_data_pipeline() does
# with that result: any scraper error is still fatal to the whole run
# (see test_tender_data_pipeline_fail_fast_preserved_on_scraper_error
# below). Stage 2/3/4 of the resilience design (partial import, changing
# which downstream steps run, telemetry) are NOT implemented here.


def _fake_runner(counts: dict):
    def _runner():
        return dict(counts)

    return _runner


def _failing_runner(message: str):
    def _runner():
        raise RuntimeError(message)

    return _runner


def test_run_tender_scrapers_one_fails_others_succeed(
    coordinator_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tender_data_pipeline_module,
        "TENDER_SCRAPER_RUNNERS",
        (
            (
                "scrape-federal",
                "Federal + MERX BC tenders",
                _fake_runner({"tenders_saved": 5}),
            ),
            (
                "scrape-merx-arch",
                "MERX architecture tenders",
                _failing_runner("arch boom"),
            ),
            (
                "scrape-commercial",
                "Commercial tenders",
                _fake_runner({"tenders_saved": 3}),
            ),
        ),
    )
    coordinator.begin_run("run-stage1-a")

    result = run_tender_scrapers("run-stage1-a")

    assert result["status"] == "partial_failure"
    assert result["steps"]["scrape-federal"]["status"] == "success"
    assert result["steps"]["scrape-federal"]["counts"] == {"tenders_saved": 5}
    assert result["steps"]["scrape-federal"]["error"] is None
    assert result["steps"]["scrape-merx-arch"]["status"] == "failed"
    assert result["steps"]["scrape-merx-arch"]["counts"] is None
    assert "arch boom" in result["steps"]["scrape-merx-arch"]["error"]
    assert result["steps"]["scrape-commercial"]["status"] == "success"
    assert result["errors"] == ["MERX architecture tenders: arch boom"]
    # Federal itself succeeded (no merx_error key in its counts) -- MERX
    # Open was attempted and succeeded.
    assert result["merx_open_status"] == "success"


def test_run_tender_scrapers_all_fail(
    coordinator_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tender_data_pipeline_module,
        "TENDER_SCRAPER_RUNNERS",
        (
            (
                "scrape-federal",
                "Federal + MERX BC tenders",
                _failing_runner("federal boom"),
            ),
            (
                "scrape-merx-arch",
                "MERX architecture tenders",
                _failing_runner("arch boom"),
            ),
            (
                "scrape-commercial",
                "Commercial tenders",
                _failing_runner("commercial boom"),
            ),
        ),
    )
    coordinator.begin_run("run-stage1-b")

    result = run_tender_scrapers("run-stage1-b")

    assert result["status"] == "failed"
    assert all(s["status"] == "failed" for s in result["steps"].values())
    assert len(result["errors"]) == 3
    # Federal itself failed -- MERX Open's own call was never reached.
    assert result["merx_open_status"] == "not_attempted"


def test_merx_open_not_attempted_when_federal_scrape_itself_fails(
    coordinator_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof: MERX Open is "not_attempted", not "failed", when Federal's
    own scrape raises before ever reaching the MERX Open call inside
    run_federal_scraper() (scraper/runners.py)."""
    monkeypatch.setattr(
        tender_data_pipeline_module,
        "TENDER_SCRAPER_RUNNERS",
        (
            (
                "scrape-federal",
                "Federal + MERX BC tenders",
                _failing_runner("federal boom"),
            ),
            (
                "scrape-merx-arch",
                "MERX architecture tenders",
                _fake_runner({"tenders_saved": 1}),
            ),
            (
                "scrape-commercial",
                "Commercial tenders",
                _fake_runner({"tenders_saved": 1}),
            ),
        ),
    )
    coordinator.begin_run("run-stage1-c")

    result = run_tender_scrapers("run-stage1-c")

    assert result["steps"]["scrape-federal"]["status"] == "failed"
    assert result["merx_open_status"] == "not_attempted"


def test_merx_open_failed_distinct_from_not_attempted(
    coordinator_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof: MERX Open is "failed", not "not_attempted", when Federal's
    own scrape succeeds but scraper.runners._scrape_merx_open_or_empty()
    caught its own error and returned merx_error -- run_federal_scraper()
    does not raise in that case (pre-existing, unchanged behavior), so
    the "scrape-federal" step itself is still "success"."""
    monkeypatch.setattr(
        tender_data_pipeline_module,
        "TENDER_SCRAPER_RUNNERS",
        (
            (
                "scrape-federal",
                "Federal + MERX BC tenders",
                _fake_runner(
                    {
                        "tenders_saved": 9,
                        "federal_saved": 9,
                        "merx_saved": 0,
                        "merx_error": "500 Server Error",
                    }
                ),
            ),
            (
                "scrape-merx-arch",
                "MERX architecture tenders",
                _fake_runner({"tenders_saved": 1}),
            ),
            (
                "scrape-commercial",
                "Commercial tenders",
                _fake_runner({"tenders_saved": 1}),
            ),
        ),
    )
    coordinator.begin_run("run-stage1-d")

    result = run_tender_scrapers("run-stage1-d")

    assert result["steps"]["scrape-federal"]["status"] == "success"
    assert result["merx_open_status"] == "failed"
    # merx_error embedded in Federal's own counts does not count as a
    # runner-level error -- this is pre-existing behavior, unchanged by
    # Stage 1.
    assert result["status"] == "success"
    assert result["errors"] == []


def test_tender_data_pipeline_hard_failure_preserved_when_all_scrapers_fail(
    coordinator_state: Path,
) -> None:
    """Stage 2 requirement: when every tender scraper fails
    (tender_scrape["status"] == "failed"), run_tender_data_pipeline()
    still raises the same RuntimeError and aborts before auxiliary
    scrapers/CSV verification/import -- exactly the pre-Stage-1/2
    fail-fast behavior. Only a TOTAL failure still hard-fails; see the
    partial_failure tests below for the case Stage 2 changes."""
    phase_log: list[str] = []

    def _fake_tender_scrapers(run_id: str) -> dict:
        phase_log.append("tender_scrape")
        return {
            "scrape_started_at": "2026-07-01T10:00:00+00:00",
            "scrape_finished_at": "2026-07-01T10:05:00+00:00",
            "status": "failed",
            "steps": {
                "scrape-federal": {
                    "status": "failed",
                    "counts": None,
                    "error": "federal boom",
                },
                "scrape-merx-arch": {
                    "status": "failed",
                    "counts": None,
                    "error": "arch boom",
                },
                "scrape-commercial": {
                    "status": "failed",
                    "counts": None,
                    "error": "commercial boom",
                },
            },
            "merx_open_status": "not_attempted",
            "errors": [
                "Federal + MERX BC tenders: federal boom",
                "MERX architecture tenders: arch boom",
                "Commercial tenders: commercial boom",
            ],
        }

    def _fake_auxiliary(*, trigger: str) -> dict:
        phase_log.append("auxiliary_scrape")
        return {}

    def _fake_csv_verify(**kwargs) -> dict:
        phase_log.append("csv_verify")
        return {}

    with patch(
        "pipeline.tender_data_pipeline.run_tender_scrapers",
        side_effect=_fake_tender_scrapers,
    ):
        with patch(
            "pipeline.tender_data_pipeline.run_auxiliary_scrapers",
            side_effect=_fake_auxiliary,
        ) as auxiliary_mock:
            with patch(
                "pipeline.tender_data_pipeline.verify_tender_csvs",
                side_effect=_fake_csv_verify,
            ) as verify_mock:
                with patch(
                    "pipeline.tender_data_pipeline.import_all_csvs"
                ) as import_mock:
                    with pytest.raises(
                        RuntimeError,
                        match=r"Tender scrape phase failed: Federal \+ MERX BC tenders: federal boom",
                    ):
                        run_tender_data_pipeline(run_id="stage2-all-fail")

    assert phase_log == ["tender_scrape"]
    auxiliary_mock.assert_not_called()
    verify_mock.assert_not_called()
    import_mock.assert_not_called()


# --- Stage 2: partial_failure imports independently-successful sources ----

_STAGE2_STEP_LABELS = {
    "scrape-federal": "Federal + MERX BC tenders",
    "scrape-merx-arch": "MERX architecture tenders",
    "scrape-commercial": "Commercial tenders",
}


def _partial_failure_tender_scrape_result(failed_step: str) -> dict:
    steps: dict[str, dict] = {}
    errors: list[str] = []
    for step in ("scrape-federal", "scrape-merx-arch", "scrape-commercial"):
        if step == failed_step:
            steps[step] = {"status": "failed", "counts": None, "error": "boom"}
            errors.append(f"{_STAGE2_STEP_LABELS[step]}: boom")
        else:
            steps[step] = {
                "status": "success",
                "counts": {"tenders_saved": 1},
                "error": None,
            }
    return {
        "scrape_started_at": "2026-07-01T10:00:00+00:00",
        "scrape_finished_at": "2026-07-01T10:05:00+00:00",
        "status": "partial_failure",
        "steps": steps,
        "merx_open_status": (
            "not_attempted" if failed_step == "scrape-federal" else "success"
        ),
        "errors": errors,
    }


def _run_partial_failure_scenario(
    coordinator_state: Path, *, failed_step: str, run_id: str
):
    """Run run_tender_data_pipeline() with exactly one tender-scraper
    step failed and the other two succeeding (Stage 2's partial-failure
    path). Returns (summary, verify_mock, import_mock, auxiliary_mock)
    so callers can assert exactly which artifacts/import keys were
    skipped."""
    result = _partial_failure_tender_scrape_result(failed_step)
    session = MagicMock()

    with patch(
        "pipeline.tender_data_pipeline.run_tender_scrapers",
        return_value=result,
    ):
        with patch(
            "pipeline.tender_data_pipeline.run_auxiliary_scrapers",
            return_value={},
        ) as auxiliary_mock:
            with patch(
                "pipeline.tender_data_pipeline.verify_tender_csvs",
                return_value={"federal_merx_tenders": 1},
            ) as verify_mock:
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
                                    "tenders": 1,
                                    "arch_tenders": 1,
                                    "commercial_tenders": 1,
                                },
                            ) as import_mock:
                                with patch(
                                    "pipeline.tender_data_pipeline.import_contract_awards",
                                    return_value=0,
                                ):
                                    with patch(
                                        "pipeline.tender_data_pipeline.refresh_company_award_stats"
                                    ):
                                        with patch(
                                            "pipeline.tender_data_pipeline.verify_database_counts",
                                            return_value={"tenders": 1},
                                        ):
                                            summary = run_tender_data_pipeline(
                                                run_id=run_id
                                            )

    return summary, verify_mock, import_mock, auxiliary_mock


def test_tender_data_pipeline_partial_failure_federal_fails_imports_arch_and_commercial(
    coordinator_state: Path,
) -> None:
    """Exact 2026-08-19 incident shape: Federal fails, MERX Architecture
    and Commercial succeed. Proves the core Stage 2 behavior: the run no
    longer aborts, auxiliary scrapers still run, only Federal's own
    CSV/import is skipped, the pipeline finishes as partial_failure
    rather than raising, and the coordinator still records success=True
    (usable data was imported)."""
    summary, verify_mock, import_mock, auxiliary_mock = _run_partial_failure_scenario(
        coordinator_state, failed_step="scrape-federal", run_id="stage2-federal-fails"
    )

    auxiliary_mock.assert_called_once()
    verify_mock.assert_called_once()
    assert verify_mock.call_args.kwargs["skip"] == frozenset({"federal_merx_tenders"})
    import_mock.assert_called_once()
    assert import_mock.call_args.kwargs["skip"] == frozenset({"tenders"})
    assert summary["status"] == "partial_failure"

    state = coordinator.get_run_state()
    assert state is not None
    assert state.success is True


def test_tender_data_pipeline_partial_failure_arch_fails_symmetric(
    coordinator_state: Path,
) -> None:
    """Symmetric case: MERX Architecture fails alone -- only its own
    artifact/import key is skipped, Federal and Commercial import
    normally."""
    summary, verify_mock, import_mock, auxiliary_mock = _run_partial_failure_scenario(
        coordinator_state, failed_step="scrape-merx-arch", run_id="stage2-arch-fails"
    )

    auxiliary_mock.assert_called_once()
    assert verify_mock.call_args.kwargs["skip"] == frozenset({"architecture_tenders"})
    assert import_mock.call_args.kwargs["skip"] == frozenset({"arch_tenders"})
    assert summary["status"] == "partial_failure"


def test_tender_data_pipeline_partial_failure_commercial_fails_symmetric(
    coordinator_state: Path,
) -> None:
    """Symmetric case: Commercial fails alone -- only its own artifact/
    import key is skipped, Federal and MERX Architecture import
    normally."""
    summary, verify_mock, import_mock, auxiliary_mock = _run_partial_failure_scenario(
        coordinator_state,
        failed_step="scrape-commercial",
        run_id="stage2-commercial-fails",
    )

    auxiliary_mock.assert_called_once()
    assert verify_mock.call_args.kwargs["skip"] == frozenset({"commercial_tenders"})
    assert import_mock.call_args.kwargs["skip"] == frozenset({"commercial_tenders"})
    assert summary["status"] == "partial_failure"


def test_tender_scrape_step_to_artifact_and_import_key_mappings_cover_all_runners() -> (
    None
):
    """Cheap insurance against the riskiest bug this design allows: a
    missing or wrong mapping entry silently skipping/verifying the wrong
    artifact. Every TENDER_SCRAPER_RUNNERS step must appear in both
    mapping constants, with no extras and no typos."""
    runner_steps = {
        step
        for step, _label, _runner in tender_data_pipeline_module.TENDER_SCRAPER_RUNNERS
    }

    assert (
        set(tender_data_pipeline_module.TENDER_SCRAPE_STEP_TO_ARTIFACT.keys())
        == runner_steps
    )
    assert (
        set(tender_data_pipeline_module.TENDER_SCRAPE_STEP_TO_IMPORT_KEY.keys())
        == runner_steps
    )
    assert tender_data_pipeline_module.TENDER_SCRAPE_STEP_TO_ARTIFACT == {
        "scrape-federal": "federal_merx_tenders",
        "scrape-merx-arch": "architecture_tenders",
        "scrape-commercial": "commercial_tenders",
    }
    assert tender_data_pipeline_module.TENDER_SCRAPE_STEP_TO_IMPORT_KEY == {
        "scrape-federal": "tenders",
        "scrape-merx-arch": "arch_tenders",
        "scrape-commercial": "commercial_tenders",
    }


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


# =======================================================================
# M3F-1: Building Permits ops_job_run telemetry (inside
# run_auxiliary_scrapers(), NOT pipeline/run.py -- see the M3F audit for
# why this lives here rather than mirroring M3D-A/B/C's location).
# =======================================================================

_BUILDING_PERMITS_SUCCESS_RESULT = {
    "source": "vancouver",
    "city": "Vancouver",
    "mode": "incremental",
    "days": 30,
    "permits_scraped": 12,
    "csv_path": "/tmp/permits.csv",
    "permits_persisted": 10,
}


def _other_auxiliary_runner(**_kwargs) -> dict:
    return {}


def _patch_auxiliary_runners(building_permits_runner):
    """Replaces AUXILIARY_SCRAPER_RUNNERS with a tuple where "Building
    permits" maps to `building_permits_runner` and every other of the 4
    entries maps to a trivial no-op fake -- so no real scraper/network/DB
    code from any other auxiliary source ever executes in these tests."""
    from pipeline.tender_data_pipeline import AUXILIARY_SCRAPER_RUNNERS as _real

    fake_runners = tuple(
        (
            label,
            (
                building_permits_runner
                if label == "Building permits"
                else _other_auxiliary_runner
            ),
        )
        for label, _runner in _real
    )
    return patch(
        "pipeline.tender_data_pipeline.AUXILIARY_SCRAPER_RUNNERS", fake_runners
    )


def test_building_permits_flag_false_calls_with_zero_kwargs(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", raising=False)
    captured_kwargs = {}

    def fake_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    with _patch_auxiliary_runners(fake_runner):
        results = run_auxiliary_scrapers()

    assert captured_kwargs == {}  # no kwargs at all -- byte-equivalent call
    assert results["Building permits"] == _BUILDING_PERMITS_SUCCESS_RESULT
    assert results["errors"] == []


def test_building_permits_flag_false_calls_no_telemetry_writer(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", raising=False)
    start_mock = MagicMock()
    finish_mock = MagicMock()

    def fake_runner(**_kwargs):
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    with patch(
        "pipeline.tender_data_pipeline._building_permits_telemetry_start", start_mock
    ):
        with patch(
            "pipeline.tender_data_pipeline._building_permits_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners(fake_runner):
                run_auxiliary_scrapers()

    start_mock.assert_not_called()
    finish_mock.assert_not_called()


def test_building_permits_flag_true_success_records_start_and_finish(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")

    def fake_runner():
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._building_permits_telemetry_start",
        return_value="run-bp-123",
    ) as start_mock:
        with patch(
            "pipeline.tender_data_pipeline._building_permits_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners(fake_runner):
                results = run_auxiliary_scrapers(trigger="scheduler")

    start_mock.assert_called_once_with(trigger="scheduler")
    finish_mock.assert_called_once_with(
        "run-bp-123",
        status="success",
        counts={"permits_scraped": 12, "permits_persisted": 10, "days": 30},
    )
    assert results["Building permits"] == _BUILDING_PERMITS_SUCCESS_RESULT
    assert results["errors"] == []


def test_building_permits_counts_exclude_mode_csv_path_source_city(
    monkeypatch,
) -> None:
    """mode, csv_path, source, and city must never reach counts -- only
    the three allowlisted flat ints."""
    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")

    def fake_runner():
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._building_permits_telemetry_start",
        return_value="run-bp-safe",
    ):
        with patch(
            "pipeline.tender_data_pipeline._building_permits_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners(fake_runner):
                run_auxiliary_scrapers()

    counts = finish_mock.call_args.kwargs["counts"]
    assert counts == {"permits_scraped": 12, "permits_persisted": 10, "days": 30}
    assert "mode" not in counts
    assert "csv_path" not in counts
    assert "source" not in counts
    assert "city" not in counts


def test_building_permits_trigger_is_not_hardcoded(monkeypatch) -> None:
    """The trigger passed to run_auxiliary_scrapers() must flow straight
    into start_job_run() -- never a hardcoded "scheduler", honoring
    whatever run_tender_data_pipeline() actually validated (scheduler or
    manual)."""
    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")

    def fake_runner():
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    session = MagicMock()
    start_job_run_mock = MagicMock(return_value="run-bp-manual")
    with patch("db.connection.get_session", return_value=session):
        with patch("pipeline.tender_data_pipeline.start_job_run", start_job_run_mock):
            with patch("pipeline.tender_data_pipeline.finish_job_run"):
                with _patch_auxiliary_runners(fake_runner):
                    run_auxiliary_scrapers(trigger="manual")

    start_job_run_mock.assert_called_once_with(
        session,
        job_type="building_permits",
        trigger="manual",
        source="permits",
    )


def test_building_permits_flag_true_exception_recorded_failed_and_reraised_to_loop(
    monkeypatch,
) -> None:
    """The runner's real exception must still land in the existing
    per-runner try/except in run_auxiliary_scrapers() (results["errors"],
    loop continues) -- unchanged by telemetry. finish(status="failed")
    fires first."""
    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")

    def raising_runner():
        raise RuntimeError("boom: sk_live_should_never_leak")

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._building_permits_telemetry_start",
        return_value="run-bp-456",
    ):
        with patch(
            "pipeline.tender_data_pipeline._building_permits_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners(raising_runner):
                results = run_auxiliary_scrapers()

    finish_mock.assert_called_once_with(
        "run-bp-456", status="failed", raw_error="boom: sk_live_should_never_leak"
    )
    assert len(results["errors"]) == 1
    assert "Building permits" in results["errors"][0]
    assert "boom: sk_live_should_never_leak" in results["errors"][0]
    # The loop kept going -- every other auxiliary source still ran and
    # produced its own (fake, empty) result.
    assert results["Vancouver early signal events"] == {}
    assert results["Reddit signals"] == {}
    assert results["News signals"] == {}
    assert results["LinkedIn signals"] == {}


def test_building_permits_flag_true_but_start_failed_still_calls_zero_kwargs(
    monkeypatch,
) -> None:
    """Fail-open: if _building_permits_telemetry_start() itself returns
    None (its own get_session()/start_job_run() failed), the real work
    must still run with the exact pre-M3F-1 zero-kwarg call, and no
    finish call is attempted (there is no run_id to finish)."""
    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")
    captured_kwargs = {}

    def fake_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._building_permits_telemetry_start",
        return_value=None,
    ):
        with patch(
            "pipeline.tender_data_pipeline._building_permits_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners(fake_runner):
                results = run_auxiliary_scrapers()

    assert captured_kwargs == {}
    finish_mock.assert_not_called()
    assert results["errors"] == []


def test_building_permits_start_get_session_failure_still_runs_once(
    monkeypatch, caplog
) -> None:
    def fake_runner(**kwargs):
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")
    with patch(
        "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
    ):
        with _patch_auxiliary_runners(fake_runner):
            with caplog.at_level("WARNING"):
                results = run_auxiliary_scrapers()

    assert results["Building permits"] == _BUILDING_PERMITS_SUCCESS_RESULT
    assert "db down" not in caplog.text
    assert "Building permits telemetry: failed to start job run tracking" in caplog.text


def test_building_permits_finish_get_session_failure_still_runs_once(
    monkeypatch, caplog
) -> None:
    def fake_runner():
        return dict(_BUILDING_PERMITS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY", "true")
    with patch(
        "pipeline.tender_data_pipeline._building_permits_telemetry_start",
        return_value="run-bp-999",
    ):
        with patch(
            "db.connection.get_session",
            MagicMock(side_effect=RuntimeError("db down")),
        ):
            with _patch_auxiliary_runners(fake_runner):
                with caplog.at_level("WARNING"):
                    results = run_auxiliary_scrapers()

    assert results["Building permits"] == _BUILDING_PERMITS_SUCCESS_RESULT
    assert "db down" not in caplog.text
    assert (
        "Building permits telemetry: failed to finish job run tracking" in caplog.text
    )


# =======================================================================
# M3F-2: Vancouver Early Signal Events ops_job_run telemetry (inside
# run_auxiliary_scrapers(), same location as M3F-1 -- see the M3F audit).
# =======================================================================

_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT = {
    "source": "vancouver_open_data",
    "dataset": "city-projects-package-site",
    "municipality": "Vancouver",
    "events_scraped": 40,
    "rezoning_applications": 15,
    "development_permit_applications": 25,
    "events_persisted": 38,
}


def _patch_auxiliary_runners_for_early_signal_events(early_signal_events_runner):
    """Replaces AUXILIARY_SCRAPER_RUNNERS with a tuple where "Vancouver
    early signal events" maps to `early_signal_events_runner` and every
    other of the 4 entries (including "Building permits") maps to a
    trivial no-op fake -- so no real scraper/network/DB code from any
    other auxiliary source ever executes in these tests."""
    from pipeline.tender_data_pipeline import AUXILIARY_SCRAPER_RUNNERS as _real

    fake_runners = tuple(
        (
            label,
            (
                early_signal_events_runner
                if label == "Vancouver early signal events"
                else _other_auxiliary_runner
            ),
        )
        for label, _runner in _real
    )
    return patch(
        "pipeline.tender_data_pipeline.AUXILIARY_SCRAPER_RUNNERS", fake_runners
    )


def test_early_signal_events_flag_false_calls_with_zero_kwargs(monkeypatch) -> None:
    monkeypatch.delenv(
        "ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", raising=False
    )
    captured_kwargs = {}

    def fake_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
        results = run_auxiliary_scrapers()

    assert captured_kwargs == {}  # no kwargs at all -- byte-equivalent call
    assert (
        results["Vancouver early signal events"] == _EARLY_SIGNAL_EVENTS_SUCCESS_RESULT
    )
    assert results["errors"] == []


def test_early_signal_events_flag_false_calls_no_telemetry_writer(monkeypatch) -> None:
    monkeypatch.delenv(
        "ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", raising=False
    )
    start_mock = MagicMock()
    finish_mock = MagicMock()

    def fake_runner(**_kwargs):
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    with patch(
        "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_start",
        start_mock,
    ):
        with patch(
            "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
                run_auxiliary_scrapers()

    start_mock.assert_not_called()
    finish_mock.assert_not_called()


def test_early_signal_events_flag_true_success_records_start_and_finish(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")

    def fake_runner():
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_start",
        return_value="run-ese-123",
    ) as start_mock:
        with patch(
            "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
                results = run_auxiliary_scrapers(trigger="scheduler")

    start_mock.assert_called_once_with(trigger="scheduler")
    finish_mock.assert_called_once_with(
        "run-ese-123",
        status="success",
        counts={
            "events_scraped": 40,
            "rezoning_applications": 15,
            "development_permit_applications": 25,
            "events_persisted": 38,
        },
    )
    assert (
        results["Vancouver early signal events"] == _EARLY_SIGNAL_EVENTS_SUCCESS_RESULT
    )
    assert results["errors"] == []


def test_early_signal_events_counts_exclude_source_dataset_municipality(
    monkeypatch,
) -> None:
    """source, dataset, and municipality must never reach counts -- only
    the four allowlisted flat ints."""
    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")

    def fake_runner():
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_start",
        return_value="run-ese-safe",
    ):
        with patch(
            "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
                run_auxiliary_scrapers()

    counts = finish_mock.call_args.kwargs["counts"]
    assert counts == {
        "events_scraped": 40,
        "rezoning_applications": 15,
        "development_permit_applications": 25,
        "events_persisted": 38,
    }
    assert "source" not in counts
    assert "dataset" not in counts
    assert "municipality" not in counts


def test_early_signal_events_trigger_is_not_hardcoded(monkeypatch) -> None:
    """The trigger passed to run_auxiliary_scrapers() must flow straight
    into start_job_run() -- never a hardcoded "scheduler", honoring
    whatever run_tender_data_pipeline() actually validated (scheduler or
    manual)."""
    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")

    def fake_runner():
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    session = MagicMock()
    start_job_run_mock = MagicMock(return_value="run-ese-manual")
    with patch("db.connection.get_session", return_value=session):
        with patch("pipeline.tender_data_pipeline.start_job_run", start_job_run_mock):
            with patch("pipeline.tender_data_pipeline.finish_job_run"):
                with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
                    run_auxiliary_scrapers(trigger="manual")

    start_job_run_mock.assert_called_once_with(
        session,
        job_type="vancouver_early_signal_events",
        trigger="manual",
        source="vancouver_open_data",
    )


def test_early_signal_events_flag_true_exception_recorded_failed_and_reraised_to_loop(
    monkeypatch,
) -> None:
    """The runner's real exception must still land in the existing
    per-runner try/except in run_auxiliary_scrapers() (results["errors"],
    loop continues) -- unchanged by telemetry. finish(status="failed")
    fires first."""
    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")

    def raising_runner():
        raise RuntimeError("boom: sk_live_should_never_leak")

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_start",
        return_value="run-ese-456",
    ):
        with patch(
            "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners_for_early_signal_events(raising_runner):
                results = run_auxiliary_scrapers()

    finish_mock.assert_called_once_with(
        "run-ese-456", status="failed", raw_error="boom: sk_live_should_never_leak"
    )
    assert len(results["errors"]) == 1
    assert "Vancouver early signal events" in results["errors"][0]
    assert "boom: sk_live_should_never_leak" in results["errors"][0]
    # The loop kept going -- every other auxiliary source still ran and
    # produced its own (fake, empty) result.
    assert results["Building permits"] == {}
    assert results["Reddit signals"] == {}
    assert results["News signals"] == {}
    assert results["LinkedIn signals"] == {}


def test_early_signal_events_flag_true_but_start_failed_still_calls_zero_kwargs(
    monkeypatch,
) -> None:
    """Fail-open: if _vancouver_early_signal_events_telemetry_start()
    itself returns None (its own get_session()/start_job_run() failed),
    the real work must still run with the exact pre-M3F-2 zero-kwarg
    call, and no finish call is attempted (there is no run_id to
    finish)."""
    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")
    captured_kwargs = {}

    def fake_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_start",
        return_value=None,
    ):
        with patch(
            "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_finish",
            finish_mock,
        ):
            with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
                results = run_auxiliary_scrapers()

    assert captured_kwargs == {}
    finish_mock.assert_not_called()
    assert results["errors"] == []


def test_early_signal_events_start_get_session_failure_still_runs_once(
    monkeypatch, caplog
) -> None:
    def fake_runner(**kwargs):
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")
    with patch(
        "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
    ):
        with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
            with caplog.at_level("WARNING"):
                results = run_auxiliary_scrapers()

    assert (
        results["Vancouver early signal events"] == _EARLY_SIGNAL_EVENTS_SUCCESS_RESULT
    )
    assert "db down" not in caplog.text
    assert (
        "Vancouver early signal events telemetry: failed to start job run tracking"
        in caplog.text
    )


def test_early_signal_events_finish_get_session_failure_still_runs_once(
    monkeypatch, caplog
) -> None:
    def fake_runner():
        return dict(_EARLY_SIGNAL_EVENTS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY", "true")
    with patch(
        "pipeline.tender_data_pipeline._vancouver_early_signal_events_telemetry_start",
        return_value="run-ese-999",
    ):
        with patch(
            "db.connection.get_session",
            MagicMock(side_effect=RuntimeError("db down")),
        ):
            with _patch_auxiliary_runners_for_early_signal_events(fake_runner):
                with caplog.at_level("WARNING"):
                    results = run_auxiliary_scrapers()

    assert (
        results["Vancouver early signal events"] == _EARLY_SIGNAL_EVENTS_SUCCESS_RESULT
    )
    assert "db down" not in caplog.text
    assert (
        "Vancouver early signal events telemetry: failed to finish job run tracking"
        in caplog.text
    )


# =======================================================================
# M3F-3: News Signals ops_job_run telemetry (inside run_auxiliary_scrapers(),
# same location as M3F-1/M3F-2 -- see the M3F audit). Unlike M3F-1/M3F-2,
# this one supports status="partial_failure" -- News Signals already has
# real per-feed fault isolation (structurally like arch_company_intelligence),
# so on_phase genuinely distinguishes a single feed's failure from success.
# =======================================================================

_NEWS_SIGNALS_SUCCESS_RESULT = {"signals_saved": 12}

_NEWS_SIGNALS_ALL_PHASES = (
    "business_in_vancouver",
    "daily_hive_vancouver",
    "vancouver_sun_business",
    "cbc_british_columbia",
)


def _patch_auxiliary_runners_for_news_signals(news_signals_runner):
    """Replaces AUXILIARY_SCRAPER_RUNNERS with a tuple where "News
    signals" maps to `news_signals_runner` and every other of the 4
    entries maps to a trivial no-op fake -- so no real scraper/network/DB
    code from any other auxiliary source ever executes in these tests."""
    from pipeline.tender_data_pipeline import AUXILIARY_SCRAPER_RUNNERS as _real

    fake_runners = tuple(
        (
            label,
            news_signals_runner if label == "News signals" else _other_auxiliary_runner,
        )
        for label, _runner in _real
    )
    return patch(
        "pipeline.tender_data_pipeline.AUXILIARY_SCRAPER_RUNNERS", fake_runners
    )


def test_news_signals_flag_false_calls_with_zero_kwargs(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", raising=False)
    captured_kwargs = {}

    def fake_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    with _patch_auxiliary_runners_for_news_signals(fake_runner):
        results = run_auxiliary_scrapers()

    assert captured_kwargs == {}  # no kwargs at all -- byte-equivalent call
    assert results["News signals"] == _NEWS_SIGNALS_SUCCESS_RESULT
    assert results["errors"] == []


def test_news_signals_flag_false_calls_no_telemetry_writer(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", raising=False)
    start_mock = MagicMock()
    finish_mock = MagicMock()

    def fake_runner(**_kwargs):
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start", start_mock
    ):
        with patch(
            "pipeline.tender_data_pipeline._news_signals_telemetry_finish", finish_mock
        ):
            with _patch_auxiliary_runners_for_news_signals(fake_runner):
                run_auxiliary_scrapers()

    start_mock.assert_not_called()
    finish_mock.assert_not_called()


def test_news_signals_flag_true_success_records_start_4_phases_and_finish_success(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")

    phases_recorded: list[str] = []

    def fake_phase(run_id, phase):
        assert run_id == "run-news-123"
        phases_recorded.append(phase)

    def fake_runner(*, on_phase=None):
        assert on_phase is not None
        for phase in _NEWS_SIGNALS_ALL_PHASES:
            on_phase(phase)
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value="run-news-123",
    ) as start_mock:
        with patch(
            "pipeline.tender_data_pipeline._news_signals_telemetry_phase", fake_phase
        ):
            with patch(
                "pipeline.tender_data_pipeline._news_signals_telemetry_finish",
                finish_mock,
            ):
                with _patch_auxiliary_runners_for_news_signals(fake_runner):
                    results = run_auxiliary_scrapers(trigger="scheduler")

    start_mock.assert_called_once_with(trigger="scheduler")
    assert phases_recorded == list(_NEWS_SIGNALS_ALL_PHASES)
    finish_mock.assert_called_once_with(
        "run-news-123", status="success", counts={"signals_saved": 12}
    )
    assert results["News signals"] == _NEWS_SIGNALS_SUCCESS_RESULT
    assert results["errors"] == []


def test_news_signals_counts_are_only_signals_saved(monkeypatch) -> None:
    """Per the accepted M3F-3 design, counts must be exactly
    {"signals_saved": int} -- no per-publisher breakdown, and any
    unexpected extra key in a future return-dict change must still be
    dropped."""
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")

    def fake_runner(*, on_phase=None):
        result = dict(_NEWS_SIGNALS_SUCCESS_RESULT)
        result["unexpected_future_field"] = "not-allowlisted"
        return result

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value="run-news-safe",
    ):
        with patch("pipeline.tender_data_pipeline._news_signals_telemetry_phase"):
            with patch(
                "pipeline.tender_data_pipeline._news_signals_telemetry_finish",
                finish_mock,
            ):
                with _patch_auxiliary_runners_for_news_signals(fake_runner):
                    run_auxiliary_scrapers()

    counts = finish_mock.call_args.kwargs["counts"]
    assert counts == {"signals_saved": 12}
    assert "unexpected_future_field" not in counts


def test_news_signals_trigger_is_not_hardcoded(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")

    def fake_runner(*, on_phase=None):
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    session = MagicMock()
    start_job_run_mock = MagicMock(return_value="run-news-manual")
    with patch("db.connection.get_session", return_value=session):
        with patch("pipeline.tender_data_pipeline.start_job_run", start_job_run_mock):
            with patch("pipeline.tender_data_pipeline.finish_job_run"):
                with _patch_auxiliary_runners_for_news_signals(fake_runner):
                    run_auxiliary_scrapers(trigger="manual")

    start_job_run_mock.assert_called_once_with(
        session,
        job_type="news_signals",
        trigger="manual",
        source=None,
    )


def test_news_signals_one_feed_failed_maps_to_partial_failure(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")

    phases_recorded: list[str] = []

    def fake_phase(run_id, phase):
        assert run_id == "run-news-partial"
        phases_recorded.append(phase)

    def fake_runner(*, on_phase=None):
        on_phase("business_in_vancouver")
        on_phase("daily_hive_vancouver_failed")  # one feed failed internally
        on_phase("vancouver_sun_business")
        on_phase("cbc_british_columbia")
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value="run-news-partial",
    ):
        with patch(
            "pipeline.tender_data_pipeline._news_signals_telemetry_phase", fake_phase
        ):
            with patch(
                "pipeline.tender_data_pipeline._news_signals_telemetry_finish",
                finish_mock,
            ):
                with _patch_auxiliary_runners_for_news_signals(fake_runner):
                    results = run_auxiliary_scrapers()

    assert "daily_hive_vancouver_failed" in phases_recorded
    finish_call = finish_mock.call_args
    assert finish_call.args == ("run-news-partial",)
    assert finish_call.kwargs["status"] == "partial_failure"
    assert results["errors"] == []  # runner() itself never raised


def test_news_signals_multiple_feeds_failed_still_one_partial_failure(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")

    def fake_runner(*, on_phase=None):
        on_phase("business_in_vancouver_failed")
        on_phase("daily_hive_vancouver")
        on_phase("vancouver_sun_business_failed")
        on_phase("cbc_british_columbia")
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value="run-news-multi",
    ):
        with patch("pipeline.tender_data_pipeline._news_signals_telemetry_phase"):
            with patch(
                "pipeline.tender_data_pipeline._news_signals_telemetry_finish",
                finish_mock,
            ):
                with _patch_auxiliary_runners_for_news_signals(fake_runner):
                    run_auxiliary_scrapers()

    finish_mock.assert_called_once()
    assert finish_mock.call_args.kwargs["status"] == "partial_failure"


def test_news_signals_flag_true_exception_recorded_failed_and_reraised_to_loop(
    monkeypatch,
) -> None:
    """An exception OUTSIDE the per-feed try/except (e.g. a CSV write
    failure) must still land in the existing per-runner try/except in
    run_auxiliary_scrapers() (results["errors"], loop continues) --
    unchanged by telemetry. finish(status="failed") fires first."""
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")

    def raising_runner(*, on_phase=None):
        raise RuntimeError("boom: sk_live_should_never_leak")

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value="run-news-456",
    ):
        with patch(
            "pipeline.tender_data_pipeline._news_signals_telemetry_finish", finish_mock
        ):
            with _patch_auxiliary_runners_for_news_signals(raising_runner):
                results = run_auxiliary_scrapers()

    finish_mock.assert_called_once_with(
        "run-news-456", status="failed", raw_error="boom: sk_live_should_never_leak"
    )
    assert len(results["errors"]) == 1
    assert "News signals" in results["errors"][0]
    assert "boom: sk_live_should_never_leak" in results["errors"][0]
    assert results["Building permits"] == {}
    assert results["Vancouver early signal events"] == {}
    assert results["Reddit signals"] == {}
    assert results["LinkedIn signals"] == {}


def test_news_signals_flag_true_but_start_failed_still_calls_zero_kwargs(
    monkeypatch,
) -> None:
    """Fail-open: if _news_signals_telemetry_start() itself returns None
    (its own get_session()/start_job_run() failed), the real work must
    still run with the exact pre-M3F-3 zero-kwarg call, and no finish
    call is attempted (there is no run_id to finish)."""
    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")
    captured_kwargs = {}

    def fake_runner(**kwargs):
        captured_kwargs.update(kwargs)
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value=None,
    ):
        with patch(
            "pipeline.tender_data_pipeline._news_signals_telemetry_finish", finish_mock
        ):
            with _patch_auxiliary_runners_for_news_signals(fake_runner):
                results = run_auxiliary_scrapers()

    assert captured_kwargs == {}
    finish_mock.assert_not_called()
    assert results["errors"] == []


def test_news_signals_start_get_session_failure_still_runs_once(
    monkeypatch, caplog
) -> None:
    def fake_runner(**kwargs):
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")
    with patch(
        "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
    ):
        with _patch_auxiliary_runners_for_news_signals(fake_runner):
            with caplog.at_level("WARNING"):
                results = run_auxiliary_scrapers()

    assert results["News signals"] == _NEWS_SIGNALS_SUCCESS_RESULT
    assert "db down" not in caplog.text
    assert "News signals telemetry: failed to start job run tracking" in caplog.text


def test_news_signals_phase_get_session_failure_still_completes_once(
    monkeypatch, caplog
) -> None:
    call_count = {"n": 0}
    real_get_session = MagicMock()

    def flaky_get_session():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_get_session
        raise RuntimeError("db down")

    def fake_runner(*, on_phase=None):
        on_phase("business_in_vancouver")
        on_phase("daily_hive_vancouver")
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")
    finish_mock = MagicMock()
    with patch(
        "pipeline.tender_data_pipeline.start_job_run", return_value="run-news-789"
    ):
        with patch("pipeline.tender_data_pipeline.finish_job_run", finish_mock):
            with patch("db.connection.get_session", flaky_get_session):
                with _patch_auxiliary_runners_for_news_signals(fake_runner):
                    with caplog.at_level("WARNING"):
                        results = run_auxiliary_scrapers()

    assert results["News signals"] == _NEWS_SIGNALS_SUCCESS_RESULT
    assert "db down" not in caplog.text
    assert (
        "News signals telemetry: failed to record phase=business_in_vancouver"
        in caplog.text
    )
    assert (
        "News signals telemetry: failed to record phase=daily_hive_vancouver"
        in caplog.text
    )
    finish_mock.assert_not_called()


def test_news_signals_finish_get_session_failure_still_runs_once(
    monkeypatch, caplog
) -> None:
    def fake_runner(*, on_phase=None):
        return dict(_NEWS_SIGNALS_SUCCESS_RESULT)

    monkeypatch.setenv("ENABLE_NEWS_SIGNALS_JOB_RUN_TELEMETRY", "true")
    with patch(
        "pipeline.tender_data_pipeline._news_signals_telemetry_start",
        return_value="run-news-999",
    ):
        with patch(
            "db.connection.get_session",
            MagicMock(side_effect=RuntimeError("db down")),
        ):
            with _patch_auxiliary_runners_for_news_signals(fake_runner):
                with caplog.at_level("WARNING"):
                    results = run_auxiliary_scrapers()

    assert results["News signals"] == _NEWS_SIGNALS_SUCCESS_RESULT
    assert "db down" not in caplog.text
    assert "News signals telemetry: failed to finish job run tracking" in caplog.text
