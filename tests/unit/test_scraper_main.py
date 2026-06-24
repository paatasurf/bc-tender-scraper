"""Unit tests for the daily scraper runner."""

from __future__ import annotations

from unittest.mock import patch

from scraper import main as scraper_main


def test_run_with_summary_records_step_failure():
    def _success() -> dict:
        return {"rows": 1}

    def _boom() -> dict:
        raise RuntimeError("ArcGIS Invalid query parameters")

    with patch(
        "scraper.main.SCRAPER_STEPS",
        (
            ("Federal + MERX BC tenders", _success),
            ("Building permits", _boom),
        ),
    ):
        summary = scraper_main.run_with_summary()

    assert summary["status"] == "failed"
    assert summary["failed_steps"] == 1
    assert summary["steps"]["Federal + MERX BC tenders"]["status"] == "success"
    assert summary["steps"]["Building permits"]["status"] == "failed"
    assert summary["errors"] == ["Building permits: ArcGIS Invalid query parameters"]


def test_run_preserves_integer_exit_status():
    with patch("scraper.main.run_with_summary", return_value={"status": "success"}):
        assert scraper_main.run() == 0

    with patch("scraper.main.run_with_summary", return_value={"status": "failed"}):
        assert scraper_main.run() == 1
