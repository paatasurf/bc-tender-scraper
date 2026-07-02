"""Regression tests for internal pipeline API response shapes."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from api import internal as internal_api


def test_tender_data_pipeline_defaults_to_tracked_background_run():
    background_tasks = MagicMock()
    started = {
        "status": "started",
        "run_id": "run-123",
        "step": "tender-data-pipeline",
        "pipeline_run_id": 42,
        "poll_url": "/internal/steps/42",
        "run_poll_url": "/internal/runs/run-123",
    }

    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch("api.internal.new_run_id", return_value="run-123"):
            with patch("api.internal._enqueue_step", return_value=started) as enqueue:
                payload = internal_api.run_tender_data_pipeline_route(
                    background_tasks,
                    None,
                    sync=False,
                )

    assert payload == started
    enqueue.assert_called_once()
    _background_tasks, step, worker, run_id = enqueue.call_args.args
    assert _background_tasks is background_tasks
    assert step == "tender-data-pipeline"
    assert run_id == "run-123"

    with patch("api.internal.run_tender_data_pipeline", return_value={"status": "success"}) as run_pipeline:
        assert worker() == {"status": "success"}
    run_pipeline.assert_called_once_with(run_id="run-123")


def test_tender_data_pipeline_sync_true_runs_inline():
    summary = {
        "status": "success",
        "run_id": "run-456",
        "phases": {"tender_scrape": {"steps": {}}},
    }

    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch("api.internal.run_tender_data_pipeline", return_value=summary) as run_pipeline:
            with patch("pipeline.run_coordinator.assert_import_not_before_scrape", return_value={"ordering_ok": "True"}):
                payload = internal_api.run_tender_data_pipeline_route(
                    MagicMock(),
                    internal_api.InternalRunRequest(run_id="run-456"),
                    sync=True,
                )

    run_pipeline.assert_called_once_with(run_id="run-456")
    assert payload == {
        "status": "success",
        "run_id": "run-456",
        "ordering_audit": {"ordering_ok": "True"},
        "phases": {"tender_scrape": {"steps": {}}},
    }


def test_enqueue_step_returns_int_pipeline_run_id():
    """_enqueue_step must return pipeline_run_id as int (not str-only dict)."""
    record = MagicMock()
    record.id = 123
    record.run_id = "run-uuid"

    session = MagicMock()
    background_tasks = MagicMock()

    with patch("api.internal.get_session", return_value=session):
        with patch("api.internal.start_run", return_value=record):
            payload = internal_api._enqueue_step(
                background_tasks,
                "arch-company-intelligence",
                lambda: {},
                None,
            )

    assert isinstance(payload["pipeline_run_id"], int)
    assert payload["poll_url"] == "/internal/steps/123"


def test_background_internal_routes_allow_non_string_response_fields():
    """FastAPI validates route return types; int fields must not use dict[str, str]."""
    for name, func in inspect.getmembers(internal_api, inspect.isfunction):
        if not name.endswith("_intelligence") and not name.startswith("scrape_"):
            continue
        if name in {"scrape_contract_awards"}:
            continue
        hints = getattr(func, "__annotations__", {})
        if "return" not in hints:
            continue
        assert hints["return"] is not str, f"{name} missing return annotation"
        assert hints["return"] != dict[str, str], (
            f"{name} must return dict[str, Any] because _enqueue_step includes int pipeline_run_id"
        )


def test_enrich_early_signals_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        try:
            internal_api.enrich_early_signals(request, MagicMock(), None)
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403


def test_populate_project_contacts_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        try:
            internal_api.populate_project_contacts(request, MagicMock(), None)
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403
