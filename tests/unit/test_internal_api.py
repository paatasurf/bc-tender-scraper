"""Regression tests for internal pipeline API response shapes."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from api import main as api_main
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


def test_trigger_google_places_returns_tracked_pipeline_run(monkeypatch):
    """Legacy manual Google Places endpoint should expose pipeline_run_id for n8n polling."""
    monkeypatch.setenv("ALLOW_MANUAL_PIPELINE", "true")

    record = MagicMock()
    record.id = 456
    record.run_id = "run-google"

    session = MagicMock()
    background_tasks = MagicMock()

    with patch("api.internal.get_session", return_value=session):
        with patch("api.internal.new_run_id", return_value="run-google"):
            with patch("api.internal.start_run", return_value=record):
                with patch("api.internal.run_tracked_step") as run_tracked_step:
                    payload = api_main.trigger_google_places(background_tasks)

    assert payload["status"] == "started"
    assert payload["step"] == "arch-google-places"
    assert payload["run_id"] == "run-google"
    assert payload["pipeline_run_id"] == 456
    assert payload["poll_url"] == "/internal/steps/456"
    background_tasks.add_task.assert_called_once_with(
        run_tracked_step,
        "arch-google-places",
        internal_steps.run_arch_google_places_step,
        run_id="run-google",
        record_id=456,
    )


def test_run_arch_google_places_step_returns_counts():
    session = MagicMock()

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.scrape_arch_companies_google.scrape_arch_companies_google",
            return_value=2,
        ) as scrape:
            with patch(
                "pipeline.arch_company_intelligence.enrich_arch_companies_google",
                return_value=3,
            ) as enrich:
                result = internal_steps.run_arch_google_places_step()

    assert result == {"scraped": 2, "enriched": 3}
    scrape.assert_called_once_with(session)
    enrich.assert_called_once_with(session)
    session.close.assert_called_once()
