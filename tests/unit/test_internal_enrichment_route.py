"""Mock-based tests for POST /internal/enrichment/company/{id}/run
(RFC Phase 2). Two behaviors under direct test, per this phase's explicit
acceptance conditions:

  1. ENRICHMENT_ENABLED=false (the default) must return 503 BEFORE any
     session, provider, or job-table function is ever touched -- no real
     job may start while the flag is off.
  2. When enabled, cache-check and in-flight dedup run synchronously
     (fast, local-DB-only) so the response is accurate immediately, and
     only a genuinely new job schedules a background task -- a cache hit
     or an already-running job must schedule NOTHING.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api import internal as internal_api


def _request(key: str | None) -> MagicMock:
    request = MagicMock()
    request.headers.get.return_value = key
    return request


def _company_mock(name: str = "Acme Co") -> MagicMock:
    # MagicMock(name=...) sets the mock's OWN repr name, not a `.name`
    # attribute -- must be assigned separately to actually stub `.name`.
    company = MagicMock()
    company.name = name
    return company


def test_flag_off_returns_503_and_touches_no_orchestrator_function():
    request = _request("secret")
    background_tasks = MagicMock()

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "false"}, clear=False):
        with patch("pipeline.company_enrichment.orchestrator.check_cache") as cache_mock:
            with patch("pipeline.company_enrichment.orchestrator.start_or_join_job") as start_mock:
                with patch("pipeline.company_enrichment.orchestrator.run_cascade_for_job") as cascade_mock:
                    with pytest.raises(Exception) as exc_info:
                        internal_api.enrichment_company_run(1, request, background_tasks, None)

    assert getattr(exc_info.value, "status_code", None) == 503
    cache_mock.assert_not_called()
    start_mock.assert_not_called()
    cascade_mock.assert_not_called()
    background_tasks.add_task.assert_not_called()


def test_flag_unset_defaults_to_disabled_same_as_explicit_false():
    """No ENRICHMENT_ENABLED in the environment at all must behave exactly
    like ENRICHMENT_ENABLED=false -- the documented default (RFC S10)."""
    request = _request("secret")
    background_tasks = MagicMock()

    env = dict(__import__("os").environ)
    env["INTERNAL_API_KEY"] = "secret"
    env.pop("ENRICHMENT_ENABLED", None)

    with patch.dict("os.environ", env, clear=True):
        with pytest.raises(Exception) as exc_info:
            internal_api.enrichment_company_run(1, request, background_tasks, None)

    assert getattr(exc_info.value, "status_code", None) == 503


def test_internal_key_still_required_even_when_flag_is_enabled():
    request = _request(None)  # no X-Internal-Key header
    background_tasks = MagicMock()

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "true"}, clear=False):
        with pytest.raises(Exception) as exc_info:
            internal_api.enrichment_company_run(1, request, background_tasks, None)

    assert getattr(exc_info.value, "status_code", None) == 403


def test_flag_on_cache_hit_returns_immediately_schedules_nothing():
    request = _request("secret")
    background_tasks = MagicMock()
    session = MagicMock()
    session.get.return_value = _company_mock()

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "true"}, clear=False):
        with patch("api.internal.get_session", return_value=session):
            with patch("db.connection.init_db"):
                with patch(
                    "pipeline.company_enrichment.orchestrator.check_cache",
                    return_value={"status": "cache_hit", "company_id": 1, "fields": []},
                ) as cache_mock:
                    with patch("pipeline.company_enrichment.orchestrator.start_or_join_job") as start_mock:
                        payload = internal_api.enrichment_company_run(1, request, background_tasks, None)

    assert payload["status"] == "cache_hit"
    cache_mock.assert_called_once()
    start_mock.assert_not_called()
    background_tasks.add_task.assert_not_called()


def test_flag_on_already_running_returns_immediately_schedules_nothing():
    request = _request("secret")
    background_tasks = MagicMock()
    session = MagicMock()
    session.get.return_value = _company_mock()

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "true"}, clear=False):
        with patch("api.internal.get_session", return_value=session):
            with patch("db.connection.init_db"):
                with patch(
                    "pipeline.company_enrichment.orchestrator.check_cache",
                    return_value=None,
                ):
                    with patch(
                        "pipeline.company_enrichment.orchestrator.start_or_join_job",
                        return_value=("existing-run-id", True),
                    ):
                        payload = internal_api.enrichment_company_run(1, request, background_tasks, None)

    assert payload == {"status": "already_running", "company_id": 1, "run_id": "existing-run-id"}
    background_tasks.add_task.assert_not_called()


def test_flag_on_new_job_schedules_exactly_one_background_task():
    request = _request("secret")
    background_tasks = MagicMock()
    session = MagicMock()
    session.get.return_value = _company_mock()

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "true"}, clear=False):
        with patch("api.internal.get_session", return_value=session):
            with patch("db.connection.init_db"):
                with patch(
                    "pipeline.company_enrichment.orchestrator.check_cache",
                    return_value=None,
                ):
                    with patch(
                        "pipeline.company_enrichment.orchestrator.start_or_join_job",
                        return_value=("new-run-id", False),
                    ):
                        payload = internal_api.enrichment_company_run(1, request, background_tasks, None)

    assert payload["status"] == "started"
    assert payload["run_id"] == "new-run-id"
    background_tasks.add_task.assert_called_once()


def test_missing_company_returns_404_before_any_cache_or_dedup_check():
    request = _request("secret")
    background_tasks = MagicMock()
    session = MagicMock()
    session.get.return_value = None

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "true"}, clear=False):
        with patch("api.internal.get_session", return_value=session):
            with patch("db.connection.init_db"):
                with patch("pipeline.company_enrichment.orchestrator.check_cache") as cache_mock:
                    with pytest.raises(Exception) as exc_info:
                        internal_api.enrichment_company_run(999999, request, background_tasks, None)

    assert getattr(exc_info.value, "status_code", None) == 404
    cache_mock.assert_not_called()
