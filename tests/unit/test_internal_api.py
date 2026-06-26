"""Regression tests for internal pipeline API response shapes."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from api import internal as internal_api
from pipeline import internal_steps


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


def test_arch_google_places_sync_uses_tracked_step():
    background_tasks = MagicMock()
    body = internal_api.InternalRunRequest(run_id="run-123")

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal._run_step_sync", return_value={"status": "success"}) as run_sync:
            payload = internal_api.arch_google_places(background_tasks, body, sync=True)

    assert payload == {"status": "success"}
    run_sync.assert_called_once_with(
        "arch-google-places",
        internal_api.run_arch_google_places_step,
        "run-123",
    )
    background_tasks.add_task.assert_not_called()


def test_arch_google_places_async_enqueues_tracked_step():
    background_tasks = MagicMock()

    with patch("api.internal._require_manual_pipeline"):
        with patch("api.internal._enqueue_step", return_value={"status": "started"}) as enqueue:
            payload = internal_api.arch_google_places(background_tasks, None)

    assert payload == {"status": "started"}
    enqueue.assert_called_once_with(
        background_tasks,
        "arch-google-places",
        internal_api.run_arch_google_places_step,
        None,
    )


def test_run_arch_google_places_step_returns_counts_and_closes_session():
    session = MagicMock()

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch("pipeline.internal_steps.scrape_arch_companies_google", return_value=3) as scrape:
            with patch("pipeline.internal_steps.enrich_arch_companies_google", return_value=5) as enrich:
                counts = internal_steps.run_arch_google_places_step()

    assert counts == {
        "arch_companies_google_scraped": 3,
        "arch_companies_google_enriched": 5,
    }
    scrape.assert_called_once_with(session)
    enrich.assert_called_once_with(session)
    session.close.assert_called_once_with()
