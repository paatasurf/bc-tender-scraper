"""Unit tests for pipeline run tracking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.runs import (
    _resolve_status,
    execute_tracked_step,
    pipeline_run_to_dict,
    run_tracked_step,
)


def _tracked(step: str, run_id: str, counts: dict) -> dict:
    import json as _json

    record = MagicMock()
    record.id = 1
    record.run_id = run_id
    record.step = step
    record.started_at = None
    record.finished_at = None

    session = MagicMock()
    session.get.return_value = record

    def _fake_finish_run(_session, _record_id, status, *, counts=None, error=None):
        record.status = status
        record.counts_json = _json.dumps(counts or {})
        record.error = error or ""
        return record

    with patch("pipeline.runs.get_session", return_value=session):
        with patch("pipeline.runs.start_run", return_value=record):
            with patch("pipeline.runs.finish_run", side_effect=_fake_finish_run):
                return execute_tracked_step(step, lambda: counts, run_id=run_id)


def test_resolve_status_legacy_skipped_flag_still_honored():
    """Non-chunked workers (no committed_chunks/write_failures) keep the old contract."""
    assert _resolve_status({"skipped": True}) == "skipped"


def test_resolve_status_legacy_worker_without_skipped_flag_is_success():
    assert _resolve_status({"total_tenders_scored": 3}) == "success"


def test_status_success_with_committed_chunk_and_no_match_records():
    """19 enriched + 6 no_match + committed_chunks=1 -> success, not skipped."""
    counts = {
        "candidates": 25,
        "fetched": 19,
        "enriched": 19,
        "no_new_values": 0,
        "skipped": 6,
        "external_failures": 0,
        "write_failures": 0,
        "committed_chunks": 1,
    }
    result = _tracked("enrich-early-signals", "run-status-1", counts)
    assert result["status"] == "success"


def test_status_skipped_when_all_candidates_are_no_ops_with_no_commits():
    """All candidates legitimately skipped, nothing committed -> skipped."""
    counts = {
        "candidates": 25,
        "fetched": 0,
        "enriched": 0,
        "skipped": 25,
        "external_failures": 0,
        "write_failures": 0,
        "committed_chunks": 0,
    }
    result = _tracked("enrich-early-signals", "run-status-2", counts)
    assert result["status"] == "skipped"


def test_status_failed_on_write_failure_with_no_commits():
    """A write failure with zero committed_chunks -> failed, not a false success."""
    counts = {
        "candidates": 5,
        "fetched": 5,
        "enriched": 0,
        "skipped": 0,
        "external_failures": 0,
        "write_failures": 1,
        "committed_chunks": 0,
    }
    result = _tracked("enrich-early-signals", "run-status-3", counts)
    assert result["status"] == "failed"


def test_status_partial_success_on_committed_chunks_then_later_failure():
    """Some chunks committed, a later chunk failed -> an explicit non-success status
    that is neither a false "success" nor a false "failed"; counts stay honest."""
    counts = {
        "candidates": 6,
        "fetched": 6,
        "enriched": 4,
        "skipped": 0,
        "external_failures": 0,
        "write_failures": 1,
        "committed_chunks": 2,
    }
    result = _tracked("enrich-early-signals", "run-status-4", counts)
    assert result["status"] == "partial_success"
    assert result["status"] != "success"
    assert result["counts"]["committed_chunks"] == 2
    assert result["counts"]["write_failures"] == 1
    assert result["counts"]["enriched"] == 4


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

    with patch("pipeline.runs.get_session", return_value=session):
        with patch("pipeline.runs.start_run", return_value=record):
            with patch("pipeline.runs.finish_run", return_value=record):
                result = execute_tracked_step("ai-scoring", _boom, run_id="run-fail")

    assert result["status"] == "failed"
    assert result["error"] == "boom"


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
