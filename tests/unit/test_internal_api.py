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


def test_ai_scoring_sync_uses_budgeted_worker():
    with patch.dict("os.environ", {"ALLOW_MANUAL_PIPELINE": "true"}, clear=False):
        with patch("api.internal._run_step_sync", return_value={"status": "success"}) as run_sync:
            payload = internal_api.ai_scoring(MagicMock(), None, True)

    assert payload == {"status": "success"}
    run_sync.assert_called_once_with(
        "ai-scoring",
        internal_api.run_ai_scoring_sync_step,
        None,
    )


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
