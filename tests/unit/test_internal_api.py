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


def test_import_csvs_sync_returns_tracked_step_payload():
    background_tasks = MagicMock()
    expected = {
        "status": "success",
        "run_id": "run-import",
        "step": "import-csvs",
        "pipeline_run_id": 321,
        "poll_url": "/internal/steps/321",
        "run_poll_url": "/internal/runs/run-import",
        "counts": {"tenders": 2},
        "error": "",
    }

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal._run_step_sync", return_value=expected) as run_step_sync:
            payload = internal_api.import_csvs(background_tasks, sync=True)

    assert payload == expected
    background_tasks.add_task.assert_not_called()
    run_step_sync.assert_called_once()
    assert run_step_sync.call_args.args[0] == "import-csvs"
    assert run_step_sync.call_args.args[1] is internal_api.run_import_step
    assert run_step_sync.call_args.args[2] is None


def test_import_contract_awards_sync_returns_tracked_step_payload():
    background_tasks = MagicMock()
    expected = {
        "status": "failed",
        "run_id": "run-awards",
        "step": "import-contract-awards",
        "pipeline_run_id": 654,
        "poll_url": "/internal/steps/654",
        "run_poll_url": "/internal/runs/run-awards",
        "counts": {},
        "error": "upstream changed payload",
    }

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal._run_step_sync", return_value=expected) as run_step_sync:
            payload = internal_api.import_contract_awards_route(background_tasks, sync=True)

    assert payload == expected
    background_tasks.add_task.assert_not_called()
    run_step_sync.assert_called_once()
    assert run_step_sync.call_args.args[0] == "import-contract-awards"
    assert run_step_sync.call_args.args[1] is internal_api.run_import_contract_awards_step
    assert run_step_sync.call_args.args[2] is None
