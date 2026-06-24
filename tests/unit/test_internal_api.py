"""Regression tests for internal pipeline API response shapes."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from api import internal as internal_api


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


def test_arch_company_intelligence_sync_runs_tracked_step():
    background_tasks = MagicMock()
    sync_payload = {
        "status": "success",
        "run_id": "run-arch",
        "step": "arch-company-intelligence",
        "pipeline_run_id": 321,
        "poll_url": "/internal/steps/321",
        "run_poll_url": "/internal/runs/run-arch",
        "started_at": None,
        "finished_at": None,
        "error": "",
        "counts": {"arch_companies_populated": 2},
    }

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal._run_step_sync", return_value=sync_payload) as run_step_sync:
            payload = internal_api.arch_company_intelligence(
                background_tasks,
                internal_api.InternalRunRequest(run_id="run-arch"),
                sync=True,
            )

    assert payload == sync_payload
    run_step_sync.assert_called_once_with(
        "arch-company-intelligence",
        internal_api.run_arch_company_intelligence_step,
        "run-arch",
    )
    background_tasks.add_task.assert_not_called()


def test_arch_company_intelligence_default_enqueues_background_step():
    background_tasks = MagicMock()
    enqueue_payload = {
        "status": "started",
        "run_id": "run-arch",
        "step": "arch-company-intelligence",
        "pipeline_run_id": 321,
        "poll_url": "/internal/steps/321",
        "run_poll_url": "/internal/runs/run-arch",
    }

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal._enqueue_step", return_value=enqueue_payload) as enqueue_step:
            payload = internal_api.arch_company_intelligence(
                background_tasks,
                internal_api.InternalRunRequest(run_id="run-arch"),
            )

    assert payload == enqueue_payload
    enqueue_step.assert_called_once_with(
        background_tasks,
        "arch-company-intelligence",
        internal_api.run_arch_company_intelligence_step,
        "run-arch",
    )
