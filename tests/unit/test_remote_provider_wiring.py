"""Tests for Phase 3F: WebsiteContactRemoteProvider's wiring into
pipeline.company_enrichment.orchestrator._default_providers().

Two layers of coverage:
1. Pure provider-composition tests (no DB, no network) -- does
   _default_providers() include/exclude the remote provider correctly
   based on ENRICHMENT_WORKER_URL's presence.
2. Real-local-Postgres integration tests (mirroring
   test_company_enrichment_orchestrator.py's own `enrichment_db` fixture
   pattern) proving the FULL cascade -- timeout/lease/dedup/write path,
   unchanged by this wiring -- behaves correctly with the real
   WebsiteContactRemoteProvider class wired in, not just a FakeProvider
   stand-in. Every outbound HTTP call is intercepted at the transport
   layer via httpx.MockTransport -- no real network call, no real worker
   process (same convention as test_remote_provider.py).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.company_enrichment_ddl import company_enrichment_migration_statements
from db.models import Company
from pipeline.company_enrichment.orchestrator import (
    _default_providers,
    run_cascade_for_job,
    start_or_join_job,
)
from pipeline.company_enrichment.remote_provider import (
    WORKER_API_KEY_ENV_VAR,
    WORKER_URL_ENV_VAR,
    WebsiteContactRemoteProvider,
)
from tests.db_test_safety import require_local_test_database

VALID_INTERNAL_URL = "http://company-enrichment-worker.railway.internal:8080"
TEST_API_KEY = "test-worker-secret-do-not-use-in-production"


def _mock_transport(handler):
    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)

    return patch(
        "pipeline.company_enrichment.remote_provider.httpx.Client",
        side_effect=_factory,
    )


# ---------------------------------------------------------------------------
# 1. Provider composition -- no DB, no network
# ---------------------------------------------------------------------------


def test_worker_url_unset_default_providers_is_orgbook_only():
    env = dict(os.environ)
    env.pop(WORKER_URL_ENV_VAR, None)
    with patch.dict(os.environ, env, clear=True):
        names = [p.name for p in _default_providers()]
    assert names == ["orgbook"]


def test_worker_url_set_default_providers_includes_remote_provider():
    with patch.dict(os.environ, {WORKER_URL_ENV_VAR: VALID_INTERNAL_URL}, clear=False):
        names = [p.name for p in _default_providers()]
    assert names == ["orgbook", "website_searxng"]


def test_worker_url_blank_string_is_treated_as_unset():
    with patch.dict(os.environ, {WORKER_URL_ENV_VAR: "   "}, clear=False):
        names = [p.name for p in _default_providers()]
    assert names == ["orgbook"]


def test_default_providers_never_needs_api_key_to_decide_inclusion():
    """The presence check is ENRICHMENT_WORKER_URL only -- a missing
    ENRICHMENT_WORKER_API_KEY does not exclude the provider from the
    tuple; it is WebsiteContactRemoteProvider.lookup()'s own job to fail
    closed with config_error when the key is absent (proven by the
    integration test below), not _default_providers()'s job to guess at
    that in advance."""
    env = dict(os.environ)
    env[WORKER_URL_ENV_VAR] = VALID_INTERNAL_URL
    env.pop(WORKER_API_KEY_ENV_VAR, None)
    with patch.dict(os.environ, env, clear=True):
        names = [p.name for p in _default_providers()]
    assert names == ["orgbook", "website_searxng"]


# ---------------------------------------------------------------------------
# 2. Existing route gate is unaffected by this wiring (disabled flag)
# ---------------------------------------------------------------------------


def test_route_still_returns_503_when_disabled_even_with_worker_url_configured():
    """ENRICHMENT_ENABLED=false must still block everything at the route
    layer, completely independent of whether ENRICHMENT_WORKER_URL is
    configured -- two separate, both-required gates, not one substituting
    for the other."""
    from api import internal as internal_api

    request = MagicMock()
    request.headers.get.return_value = "secret"
    background_tasks = MagicMock()

    with patch.dict(
        os.environ,
        {
            "INTERNAL_API_KEY": "secret",
            "ENRICHMENT_ENABLED": "false",
            WORKER_URL_ENV_VAR: VALID_INTERNAL_URL,
            WORKER_API_KEY_ENV_VAR: TEST_API_KEY,
        },
        clear=False,
    ):
        with patch(
            "pipeline.company_enrichment.orchestrator.start_or_join_job"
        ) as start_mock:
            with patch(
                "pipeline.company_enrichment.orchestrator.run_cascade_for_job"
            ) as cascade_mock:
                with pytest.raises(Exception) as exc_info:
                    internal_api.enrichment_company_run(
                        1, request, background_tasks, None
                    )

    assert getattr(exc_info.value, "status_code", None) == 503
    start_mock.assert_not_called()
    cascade_mock.assert_not_called()
    background_tasks.add_task.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Full-cascade integration -- real local Postgres, mocked HTTP only
# ---------------------------------------------------------------------------


@pytest.fixture
def enrichment_db():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in company_enrichment_migration_statements():
            conn.execute(text(statement))

    with Session(engine) as session:
        company = Company(name="Remote Provider Wiring Test Co Ltd")
        session.add(company)
        session.commit()
        company_id = company.id

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM company_enrichment_fields WHERE company_id = :id"),
                {"id": company_id},
            )
            conn.execute(
                text("DELETE FROM company_enrichment_jobs WHERE company_id = :id"),
                {"id": company_id},
            )
            conn.execute(
                text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
            )

    try:
        yield engine, company_id
    finally:
        _reset()
        engine.dispose()


def _field_rows(engine, company_id: int) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT field_name, value, source FROM company_enrichment_fields "
                    "WHERE company_id = :id"
                ),
                {"id": company_id},
            )
            .mappings()
            .all()
        ]


def test_missing_configuration_fails_closed_no_writes(enrichment_db):
    """ENRICHMENT_WORKER_URL set (so the provider is wired in) but
    ENRICHMENT_WORKER_API_KEY missing -- must fail closed: a clean
    'error' tag in providers_attempted, zero rows written, job still
    reaches a terminal status (never left 'running')."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    env = dict(os.environ)
    env[WORKER_URL_ENV_VAR] = VALID_INTERNAL_URL
    env.pop(WORKER_API_KEY_ENV_VAR, None)
    with patch.dict(os.environ, env, clear=True):
        with Session(engine) as session:
            result = run_cascade_for_job(
                session,
                run_id,
                company_id,
                "Remote Provider Wiring Test Co Ltd",
                providers=(WebsiteContactRemoteProvider(),),
            )

    assert result["status"] == "failed"
    assert result["providers_attempted"] == ["website_searxng:error"]
    assert _field_rows(engine, company_id) == []


def test_successful_remote_provider_selection_writes_facts(enrichment_db):
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"provider":"website_searxng","matched":true,"facts":'
                b'[{"field_name":"phone","value":"604-555-0100","confidence":0.9,'
                b'"source_url":null,"raw_value":null,"extraction_method":null}],'
                b'"error":null}'
            ),
        )

    with patch.dict(
        os.environ,
        {WORKER_URL_ENV_VAR: VALID_INTERNAL_URL, WORKER_API_KEY_ENV_VAR: TEST_API_KEY},
        clear=False,
    ):
        with _mock_transport(handler):
            with Session(engine) as session:
                result = run_cascade_for_job(
                    session,
                    run_id,
                    company_id,
                    "Remote Provider Wiring Test Co Ltd",
                    providers=(WebsiteContactRemoteProvider(),),
                )

    assert result["status"] == "success"
    assert result["providers_attempted"] == ["website_searxng:ok"]
    rows = _field_rows(engine, company_id)
    assert len(rows) == 1
    assert rows[0]["field_name"] == "phone"
    assert rows[0]["value"] == "604-555-0100"
    assert rows[0]["source"] == "website_searxng"


def test_timeout_is_isolated_job_still_reaches_terminal_status(enrichment_db):
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    with patch.dict(
        os.environ,
        {WORKER_URL_ENV_VAR: VALID_INTERNAL_URL, WORKER_API_KEY_ENV_VAR: TEST_API_KEY},
        clear=False,
    ):
        with _mock_transport(handler):
            with Session(engine) as session:
                result = run_cascade_for_job(
                    session,
                    run_id,
                    company_id,
                    "Remote Provider Wiring Test Co Ltd",
                    providers=(WebsiteContactRemoteProvider(),),
                    timeout_s=5.0,
                )

    # The provider's own httpx timeout fires first and returns a clean
    # ProviderResult(error="timeout:...") -- _call_provider_with_timeout
    # sees a normal return (not a hang), so the cascade-level tag is
    # "error" (matches _resolve_cascade_status's own truth table), not a
    # cascade-level "timeout" -- that tag is reserved for a provider that
    # never returns at all within timeout_s.
    assert result["status"] == "failed"
    assert result["providers_attempted"] == ["website_searxng:error"]
    assert _field_rows(engine, company_id) == []


def test_worker_5xx_error_is_isolated_job_still_reaches_terminal_status(enrichment_db):
    engine, company_id = enrichment_db
    with Session(engine) as session:
        run_id, _ = start_or_join_job(session, company_id, trigger="profile_view")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b'{"detail":"Service Unavailable"}')

    with patch.dict(
        os.environ,
        {WORKER_URL_ENV_VAR: VALID_INTERNAL_URL, WORKER_API_KEY_ENV_VAR: TEST_API_KEY},
        clear=False,
    ):
        with _mock_transport(handler):
            with Session(engine) as session:
                result = run_cascade_for_job(
                    session,
                    run_id,
                    company_id,
                    "Remote Provider Wiring Test Co Ltd",
                    providers=(WebsiteContactRemoteProvider(),),
                )

    assert result["status"] == "failed"
    assert result["providers_attempted"] == ["website_searxng:error"]
    assert _field_rows(engine, company_id) == []
