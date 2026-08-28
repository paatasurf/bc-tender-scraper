"""Regression tests for internal pipeline API response shapes."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

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
        assert (
            hints["return"] != dict[str, str]
        ), f"{name} must return dict[str, Any] because _enqueue_step includes int pipeline_run_id"


def test_enrich_early_signals_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        try:
            internal_api.enrich_early_signals(request, MagicMock(), None)
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403


def test_enrich_early_signals_default_limit_preserves_existing_behavior():
    """Omitting `limit` must call the runner exactly as scheduled/n8n triggers do today."""
    request = MagicMock()
    request.headers.get.return_value = "secret"

    captured = {}

    def _fake_enqueue_step(background_tasks, step, worker, run_id):
        captured["worker"] = worker
        captured["run_id"] = run_id
        return {"status": "started"}

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch("api.internal._enqueue_step", side_effect=_fake_enqueue_step):
            with patch(
                "api.internal.run_vancouver_early_signal_enrichment_scraper",
                return_value={"candidates": 0},
            ) as fake_runner:
                internal_api.enrich_early_signals(request, MagicMock(), None)
                captured["worker"]()
                fake_runner.assert_called_once_with(limit=None)

    assert captured["run_id"] is None


def test_enrich_early_signals_passes_validated_canary_limit_and_run_id():
    """An authenticated canary body must thread limit into the runner and keep run_id findable."""
    request = MagicMock()
    request.headers.get.return_value = "secret"

    captured = {}

    def _fake_enqueue_step(background_tasks, step, worker, run_id):
        captured["worker"] = worker
        captured["run_id"] = run_id
        return {"status": "started"}

    body = internal_api.EnrichEarlySignalsRequest(
        run_id="early-signal-canary-abc123", limit=25
    )

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch("api.internal._enqueue_step", side_effect=_fake_enqueue_step):
            with patch(
                "api.internal.run_vancouver_early_signal_enrichment_scraper",
                return_value={"candidates": 25},
            ) as fake_runner:
                internal_api.enrich_early_signals(request, MagicMock(), body)
                captured["worker"]()
                fake_runner.assert_called_once_with(limit=25)

    assert captured["run_id"] == "early-signal-canary-abc123"


def test_enrich_early_signals_request_rejects_limit_above_25():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(limit=26)


def test_enrich_early_signals_request_rejects_limit_below_1():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(limit=0)


def test_enrich_early_signals_request_rejects_non_integer_limit():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(limit="not-a-number")


def test_populate_project_contacts_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        try:
            internal_api.populate_project_contacts(request, MagicMock(), None)
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403


def test_google_enrichment_metrics_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        try:
            internal_api.google_enrichment_metrics(request)
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403


def test_google_enrichment_run_requires_internal_key():
    request = MagicMock()
    request.headers.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        try:
            internal_api.google_enrichment_run(request, None)
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403
