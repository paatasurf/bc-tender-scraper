"""Unit tests for pipeline run tracking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.runs import execute_tracked_step, pipeline_run_to_dict, run_tracked_step


def test_execute_tracked_step_returns_counts():
    record = MagicMock()
    record.id = 42
    record.run_id = "run-abc"
    record.step = "ai-scoring"
    record.status = "success"
    record.started_at = None
    record.finished_at = None
    record.error = ""
    record.counts_json = '{"total_tenders_scored": 3}'

    session = MagicMock()
    session.get.return_value = record

    with patch("pipeline.runs.get_session", return_value=session):
        with patch("pipeline.runs.start_run", return_value=record):
            with patch("pipeline.runs.finish_run", return_value=record):
                result = execute_tracked_step(
                    "ai-scoring",
                    lambda: {"total_tenders_scored": 3},
                    run_id="run-abc",
                )

    assert result["status"] == "success"
    assert result["counts"]["total_tenders_scored"] == 3
    assert result["run_id"] == "run-abc"


def test_execute_tracked_step_marks_failed_runs():
    record = MagicMock()
    record.id = 7
    record.run_id = "run-fail"
    record.step = "ai-scoring"
    record.status = "failed"
    record.started_at = None
    record.finished_at = None
    record.error = "boom"
    record.counts_json = "{}"

    session = MagicMock()
    session.get.return_value = record

    def _boom() -> dict:
        raise RuntimeError("boom")

    def _finish_run(_session, _record_id, status, *, counts=None, error=None):
        record.status = status
        record.error = error
        record.counts_json = "{}"
        return record

    with patch("pipeline.runs.get_session", return_value=session):
        with patch("pipeline.runs.start_run", return_value=record):
            with patch("pipeline.runs.finish_run", side_effect=_finish_run):
                result = execute_tracked_step("ai-scoring", _boom, run_id="run-fail")

    assert result["status"] == "failed"
    assert result["error"] == "RuntimeError: boom"


def test_execute_tracked_step_records_exception_type_for_blank_message():
    record = MagicMock()
    record.id = 8
    record.run_id = "run-blank-fail"
    record.step = "import-csvs"
    record.status = "failed"
    record.started_at = None
    record.finished_at = None
    record.error = "RuntimeError"
    record.counts_json = "{}"

    session = MagicMock()
    session.get.return_value = record

    def _blank_error() -> dict:
        raise RuntimeError()

    def _finish_run(_session, _record_id, status, *, counts=None, error=None):
        record.status = status
        record.error = error
        record.counts_json = "{}"
        return record

    with patch("pipeline.runs.get_session", return_value=session):
        with patch("pipeline.runs.start_run", return_value=record):
            with patch("pipeline.runs.finish_run", side_effect=_finish_run):
                result = execute_tracked_step("import-csvs", _blank_error, run_id="run-blank-fail")

    assert result["status"] == "failed"
    assert result["error"] == "RuntimeError"


def test_run_tracked_step_uses_existing_record_id():
    existing = MagicMock()
    existing.id = 99
    existing.run_id = "existing-run"

    session = MagicMock()
    session.get.return_value = existing

    with patch("pipeline.runs.get_session", return_value=session):
        with patch("pipeline.runs.finish_run", return_value=existing) as finish_run:
            run_tracked_step(
                "ai-scoring",
                lambda: {"total_tenders_scored": 1},
                record_id=99,
            )

    finish_run.assert_called_once()
    assert finish_run.call_args.args[1] == 99
    assert finish_run.call_args.args[2] == "success"


def test_pipeline_run_to_dict_parses_counts_json():
    record = MagicMock()
    record.id = 1
    record.run_id = "abc"
    record.step = "ai-scoring"
    record.status = "running"
    record.started_at = None
    record.finished_at = None
    record.error = ""
    record.counts_json = '{"total_tenders_scored": 5}'

    payload = pipeline_run_to_dict(record)
    assert payload["counts"]["total_tenders_scored"] == 5
