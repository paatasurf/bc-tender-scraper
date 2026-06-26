"""Regression tests for internal pipeline API response shapes."""

from __future__ import annotations

import inspect
from typing import Callable
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


def test_internal_surrey_permits_sync_route_tracks_named_step_and_days():
    request = internal_api.InternalScrapeRunRequest(run_id="run-surrey", days=14)

    def run_sync(step: str, worker: Callable[[], dict], run_id: str | None) -> dict:
        return {"step": step, "run_id": run_id, "counts": worker()}

    with (
        patch("api.internal._require_manual_pipeline"),
        patch("api.internal._run_step_sync", side_effect=run_sync),
        patch(
            "api.internal.run_surrey_permits_scraper",
            return_value={"permits_scraped": 3, "permits_persisted": 3},
        ) as scraper,
    ):
        payload = internal_api.scrape_surrey_permits(
            MagicMock(),
            body=request,
            sync=True,
        )

    scraper.assert_called_once_with(days=14)
    assert payload == {
        "step": "scrape-surrey-permits",
        "run_id": "run-surrey",
        "counts": {"permits_scraped": 3, "permits_persisted": 3},
    }
