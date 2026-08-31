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


def test_enqueue_step_reuses_in_flight_run_instead_of_starting_a_second_worker():
    """Reproduces the fix for pipeline_runs id 754/755 (step="import-csvs",
    run_id="n8n-5564", 2026-08-05): a second HTTP trigger for the same
    explicit run_id while the first is still "running" must not call
    start_run() again or schedule a second background worker -- it must
    reuse the existing in-flight run's identity."""
    in_flight = MagicMock()
    in_flight.id = 777

    session = MagicMock()
    background_tasks = MagicMock()

    with patch("api.internal.get_session", return_value=session):
        with patch("api.internal.find_in_flight_run", return_value=in_flight):
            with patch("api.internal.start_run") as start_run_mock:
                payload = internal_api._enqueue_step(
                    background_tasks,
                    "import-csvs",
                    lambda: {},
                    "n8n-5564",
                )

    assert payload["status"] == "already_running"
    assert payload["pipeline_run_id"] == 777
    assert payload["run_id"] == "n8n-5564"
    assert payload["poll_url"] == "/internal/steps/777"
    start_run_mock.assert_not_called()
    background_tasks.add_task.assert_not_called()


def test_enqueue_step_starts_normally_when_nothing_is_in_flight():
    """Regression guard: the no-collision path is unchanged -- start_run()
    is still called and a worker is still scheduled when
    find_in_flight_run() finds nothing."""
    record = MagicMock()
    record.id = 888
    record.run_id = "n8n-9999"

    session = MagicMock()
    background_tasks = MagicMock()

    with patch("api.internal.get_session", return_value=session):
        with patch("api.internal.find_in_flight_run", return_value=None):
            with patch("api.internal.start_run", return_value=record):
                payload = internal_api._enqueue_step(
                    background_tasks,
                    "import-csvs",
                    lambda: {},
                    "n8n-9999",
                )

    assert payload["status"] == "started"
    assert payload["pipeline_run_id"] == 888
    background_tasks.add_task.assert_called_once()


def test_enqueue_step_skips_in_flight_check_when_run_id_is_omitted():
    """When the caller supplies no explicit run_id, a fresh UUID is always
    generated -- there is nothing it could collide with, so the dedup
    query must not even run."""
    record = MagicMock()
    record.id = 999
    record.run_id = "generated-uuid"

    session = MagicMock()
    background_tasks = MagicMock()

    with patch("api.internal.get_session", return_value=session):
        with patch("api.internal.find_in_flight_run") as find_mock:
            with patch("api.internal.start_run", return_value=record):
                payload = internal_api._enqueue_step(
                    background_tasks,
                    "import-csvs",
                    lambda: {},
                    None,
                )

    assert payload["status"] == "started"
    find_mock.assert_not_called()


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
                fake_runner.assert_called_once_with(
                    limit=None, since_id=None, refresh_all=False
                )

    assert captured["run_id"] is None


def test_enrich_early_signals_passes_validated_canary_limit_and_run_id():
    """An authenticated canary body must thread limit/since_id/refresh_all into the runner and keep run_id findable."""
    request = MagicMock()
    request.headers.get.return_value = "secret"

    captured = {}

    def _fake_enqueue_step(background_tasks, step, worker, run_id):
        captured["worker"] = worker
        captured["run_id"] = run_id
        return {"status": "started"}

    body = internal_api.EnrichEarlySignalsRequest(
        run_id="early-signal-canary-abc123", limit=25, since_id=100
    )

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch("api.internal._enqueue_step", side_effect=_fake_enqueue_step):
            with patch(
                "api.internal.run_vancouver_early_signal_enrichment_scraper",
                return_value={"candidates": 25},
            ) as fake_runner:
                internal_api.enrich_early_signals(request, MagicMock(), body)
                captured["worker"]()
                fake_runner.assert_called_once_with(
                    limit=25, since_id=100, refresh_all=False
                )

    assert captured["run_id"] == "early-signal-canary-abc123"


def test_enrich_early_signals_forwards_explicit_refresh_all():
    request = MagicMock()
    request.headers.get.return_value = "secret"

    captured = {}

    def _fake_enqueue_step(background_tasks, step, worker, run_id):
        captured["worker"] = worker
        return {"status": "started"}

    body = internal_api.EnrichEarlySignalsRequest(refresh_all=True)

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret"}, clear=False):
        with patch("api.internal._enqueue_step", side_effect=_fake_enqueue_step):
            with patch(
                "api.internal.run_vancouver_early_signal_enrichment_scraper",
                return_value={"candidates": 0},
            ) as fake_runner:
                internal_api.enrich_early_signals(request, MagicMock(), body)
                captured["worker"]()
                fake_runner.assert_called_once_with(
                    limit=None, since_id=None, refresh_all=True
                )


def test_enrich_early_signals_request_rejects_negative_since_id():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(since_id=-1)


def test_enrich_early_signals_request_rejects_limit_above_25():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(limit=26)


def test_enrich_early_signals_request_rejects_limit_below_1():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(limit=0)


def test_enrich_early_signals_request_rejects_non_integer_limit():
    with pytest.raises(ValidationError):
        internal_api.EnrichEarlySignalsRequest(limit="not-a-number")


def _fake_pipeline_step_record(status: str) -> MagicMock:
    record = MagicMock()
    record.id = 5
    record.run_id = "run-status-check"
    record.step = "enrich-early-signals"
    record.status = status
    record.started_at = None
    record.finished_at = None
    record.error = ""
    record.counts_json = "{}"
    return record


def test_pipeline_step_status_done_true_for_partial_success():
    record = _fake_pipeline_step_record("partial_success")

    with patch("api.internal.get_session", return_value=MagicMock()):
        with patch("api.internal.get_pipeline_run", return_value=record):
            payload = internal_api.get_pipeline_step_status(5)

    assert payload["status"] == "partial_success"
    assert payload["done"] is True


def test_pipeline_step_status_done_field_unchanged_for_other_statuses():
    """success/failed/skipped stay done=True; running stays done=False."""
    for status, expected_done in (
        ("success", True),
        ("failed", True),
        ("skipped", True),
        ("running", False),
    ):
        record = _fake_pipeline_step_record(status)
        with patch("api.internal.get_session", return_value=MagicMock()):
            with patch("api.internal.get_pipeline_run", return_value=record):
                payload = internal_api.get_pipeline_step_status(5)
        assert payload["done"] is expected_done, status


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
