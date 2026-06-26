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


def test_arch_company_intelligence_sync_returns_completion_payload():
    """Arch n8n workflows can request blocking execution, matching ai-scoring."""
    background_tasks = MagicMock()
    request = internal_api.InternalRunRequest(run_id="run-arch")

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal.execute_tracked_step") as execute_tracked_step:
            execute_tracked_step.return_value = {
                "id": 456,
                "status": "success",
                "started_at": "2026-06-26T10:00:00+00:00",
                "finished_at": "2026-06-26T10:01:00+00:00",
                "error": "",
                "counts": {"arch_companies_populated": 12},
            }

            payload = internal_api.arch_company_intelligence(
                background_tasks,
                request,
                sync=True,
            )

    execute_tracked_step.assert_called_once()
    assert execute_tracked_step.call_args.args[0] == "arch-company-intelligence"
    assert payload == {
        "status": "success",
        "run_id": "run-arch",
        "step": "arch-company-intelligence",
        "pipeline_run_id": 456,
        "poll_url": "/internal/steps/456",
        "run_poll_url": "/internal/runs/run-arch",
        "started_at": "2026-06-26T10:00:00+00:00",
        "finished_at": "2026-06-26T10:01:00+00:00",
        "error": "",
        "counts": {"arch_companies_populated": 12},
    }
    background_tasks.add_task.assert_not_called()


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
