"""Unit tests for daily pipeline orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline import run as pipeline_run


def _tracked_result(status: str = "success", error: str = "") -> dict:
    return {"status": status, "error": error, "counts": {}}


def test_run_pipeline_tracks_daily_phases_under_one_run_id():
    steps: list[tuple[str, str]] = []
    session = MagicMock()

    def _execute(step, worker, *, run_id):
        steps.append((step, run_id))
        return _tracked_result()

    with (
        patch("pipeline.run.new_run_id", return_value="daily-run"),
        patch("pipeline.run.execute_tracked_step", side_effect=_execute),
        patch("pipeline.run.env_flag", return_value=False),
        patch("pipeline.run.get_session", return_value=session),
    ):
        assert pipeline_run.run_pipeline() == 0

    assert steps == [
        ("daily-scrapers", "daily-run"),
        ("import-csvs", "daily-run"),
        ("import-contract-awards", "daily-run"),
        ("refresh-company-award-stats", "daily-run"),
        ("ai-scoring", "daily-run"),
        ("company-intelligence", "daily-run"),
        ("arch-company-intelligence", "daily-run"),
    ]


def test_run_pipeline_continues_after_scraper_failure_but_returns_failure():
    steps: list[str] = []

    def _execute(step, worker, *, run_id):
        steps.append(step)
        if step == "daily-scrapers":
            return _tracked_result("failed", "Building permits: boom")
        return _tracked_result()

    with (
        patch("pipeline.run.new_run_id", return_value="daily-run"),
        patch("pipeline.run.execute_tracked_step", side_effect=_execute),
        patch("pipeline.run.env_flag", return_value=False),
        patch("pipeline.run.get_session", return_value=MagicMock()),
    ):
        assert pipeline_run.run_pipeline() == 1

    assert "import-csvs" in steps
    assert steps[-1] == "arch-company-intelligence"


def test_run_pipeline_aborts_after_import_failure():
    steps: list[str] = []

    def _execute(step, worker, *, run_id):
        steps.append(step)
        if step == "import-csvs":
            return _tracked_result("failed", "database import failed")
        return _tracked_result()

    with (
        patch("pipeline.run.new_run_id", return_value="daily-run"),
        patch("pipeline.run.execute_tracked_step", side_effect=_execute),
        patch("pipeline.run.env_flag", return_value=False),
    ):
        assert pipeline_run.run_pipeline() == 1

    assert steps == ["daily-scrapers", "import-csvs"]
