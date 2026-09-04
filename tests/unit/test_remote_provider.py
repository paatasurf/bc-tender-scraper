"""Unit tests for WebsiteContactRemoteProvider
(pipeline/company_enrichment/remote_provider.py, Phase 3E).

Every outbound HTTP call is intercepted at the transport layer via
`httpx.MockTransport` -- no real network call, no real worker process,
matching this repo's existing "mock the network boundary, never touch a
real socket" convention (test_website_contact_provider.py,
test_enrichment_worker.py). `httpx.Client` itself is patched to always
attach the mock transport regardless of what real `ENRICHMENT_WORKER_URL`
the test configured -- the URL still has to pass this provider's own
validation first (that's the whole point of several tests below), it
just never actually gets dialed.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from pipeline.company_enrichment.provider import EnrichmentRequest
from pipeline.company_enrichment.remote_provider import (
    WORKER_API_KEY_ENV_VAR,
    WORKER_AUTH_HEADER,
    WORKER_URL_ENV_VAR,
    WebsiteContactRemoteProvider,
)

VALID_INTERNAL_URL = "http://company-enrichment-worker.railway.internal:8080"
TEST_API_KEY = "test-worker-secret-do-not-use-in-production"


def _env(**overrides) -> dict[str, str]:
    base = {
        WORKER_URL_ENV_VAR: VALID_INTERNAL_URL,
        WORKER_API_KEY_ENV_VAR: TEST_API_KEY,
    }
    base.update(overrides)
    return base


def _request() -> EnrichmentRequest:
    return EnrichmentRequest(
        company_id=1, company_name="Acme Co", website="example.com"
    )


def _mock_transport(handler):
    """Patches httpx.Client so every instance it constructs uses the
    given MockTransport handler instead of a real network connection."""
    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)

    return patch(
        "pipeline.company_enrichment.remote_provider.httpx.Client",
        side_effect=_factory,
    )


def _json_handler(status_code: int, body: bytes | dict):
    import json as _json

    payload = (
        body if isinstance(body, (bytes, bytearray)) else _json.dumps(body).encode()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=payload)

    return handler


def _raising_handler(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


# ---------------------------------------------------------------------------
# 1. Missing env
# ---------------------------------------------------------------------------


def test_missing_worker_url_returns_config_error():
    env = _env()
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop(WORKER_URL_ENV_VAR, None)
        result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("config_error:")
    assert WORKER_URL_ENV_VAR in result.error


def test_missing_api_key_returns_config_error():
    env = _env()
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop(WORKER_API_KEY_ENV_VAR, None)
        result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("config_error:")
    assert WORKER_API_KEY_ENV_VAR in result.error


# ---------------------------------------------------------------------------
# 2-3. Invalid / public worker URL rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://company-enrichment-worker.railway.internal:8080",  # bad scheme
        "http://user:pass@company-enrichment-worker.railway.internal:8080",  # embedded creds
        "https://company-enrichment-worker-production.up.railway.app",  # public Railway domain
        "https://foo.rlwy.net",  # public Railway proxy domain
        "http://localhost:8080",  # localhost
        "http://127.0.0.1:8080",  # IP literal
        "https://evil.com",  # arbitrary public host
        "https://railway.internal.attacker.com",  # suffix-spoofing attempt (contains, doesn't end with)
    ],
)
def test_invalid_or_public_worker_url_rejected(bad_url: str):
    with patch.dict(os.environ, _env(**{WORKER_URL_ENV_VAR: bad_url}), clear=False):
        result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("config_error:")


def test_invalid_url_error_never_leaks_embedded_password():
    bad_url = "http://user:hunter2@company-enrichment-worker.railway.internal:8080"
    with patch.dict(os.environ, _env(**{WORKER_URL_ENV_VAR: bad_url}), clear=False):
        result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert "hunter2" not in (result.error or "")


# ---------------------------------------------------------------------------
# 4. Auth header sent, secret never logged
# ---------------------------------------------------------------------------


def test_auth_header_sent_and_api_key_never_appears_in_logs(caplog):
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(
            200,
            content=b'{"provider":"website_searxng","matched":false,"facts":[],"error":null}',
        )

    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            with caplog.at_level(logging.DEBUG):
                WebsiteContactRemoteProvider().lookup(MagicMock(), _request())

    assert captured_headers.get(WORKER_AUTH_HEADER.lower()) == TEST_API_KEY
    for record in caplog.records:
        assert TEST_API_KEY not in record.getMessage()


def test_wrong_env_key_value_never_logged_on_auth_failure(caplog):
    """Even the FAILURE paths (401/403 from the worker) must never log
    the key this provider sent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b'{"detail":"Forbidden"}')

    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            with caplog.at_level(logging.DEBUG):
                WebsiteContactRemoteProvider().lookup(MagicMock(), _request())

    for record in caplog.records:
        assert TEST_API_KEY not in record.getMessage()


# ---------------------------------------------------------------------------
# 5. Timeout
# ---------------------------------------------------------------------------


def test_timeout_returns_timeout_tag():
    handler = _raising_handler(httpx.ReadTimeout("simulated timeout"))
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("timeout:")


# ---------------------------------------------------------------------------
# 6. 401 / 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_status_returns_auth_error(status: int):
    handler = _json_handler(status, {"detail": "Forbidden"})
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error == f"auth_error:http_{status}"


# ---------------------------------------------------------------------------
# 7. 5xx / network error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_returns_worker_unavailable(status: int):
    handler = _json_handler(status, {"detail": "Internal error"})
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error == f"worker_unavailable:http_{status}"


def test_network_error_returns_worker_unavailable():
    handler = _raising_handler(httpx.ConnectError("simulated connection refused"))
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("worker_unavailable:")


# ---------------------------------------------------------------------------
# 8. Malformed JSON
# ---------------------------------------------------------------------------


def test_malformed_json_returns_malformed_response():
    handler = _json_handler(200, b"not valid json {{{")
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("malformed_response:")


def test_response_missing_required_fields_returns_malformed_response():
    handler = _json_handler(200, {"unexpected": "shape"})
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is not None
    assert result.error.startswith("malformed_response:")


# ---------------------------------------------------------------------------
# 9. Oversized response
# ---------------------------------------------------------------------------


def test_oversized_response_returns_malformed_response():
    huge_facts = [
        {"field_name": "phone", "value": "x" * 200} for _ in range(1000)
    ]  # comfortably over MAX_RESPONSE_BYTES once serialized
    handler = _json_handler(
        200,
        {
            "provider": "website_searxng",
            "matched": True,
            "facts": huge_facts,
            "error": None,
        },
    )
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error == "malformed_response:oversized"


def test_response_at_exactly_the_size_cap_is_not_rejected_solely_for_size():
    # A small, well-under-cap valid response should never be flagged as
    # oversized -- sanity check that the cap logic isn't inverted.
    handler = _json_handler(
        200,
        {"provider": "website_searxng", "matched": False, "facts": [], "error": None},
    )
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.error != "malformed_response:oversized"


# ---------------------------------------------------------------------------
# 10. Successful response preserves all facts and metadata
# ---------------------------------------------------------------------------


def test_successful_response_preserves_all_facts_and_metadata_losslessly():
    body = {
        "provider": "website_searxng",
        "matched": True,
        "facts": [
            {
                "field_name": "phone",
                "value": "604-555-0100",
                "confidence": 0.9,
                "source_url": "https://example.com/contact",
                "raw_value": "Call us: 604-555-0100",
                "extraction_method": "regex",
            },
            {
                "field_name": "email",
                "value": "info@example.com",
                "confidence": None,
                "source_url": None,
                "raw_value": None,
                "extraction_method": None,
            },
        ],
        "error": None,
        "correlation_id": "11111111-1111-1111-1111-111111111111",
    }
    handler = _json_handler(200, body)
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())

    assert result.provider == "website_searxng"
    assert result.matched is True
    assert result.error is None
    assert len(result.facts) == 2

    phone = result.facts[0]
    assert phone.field_name == "phone"
    assert phone.value == "604-555-0100"
    assert phone.confidence == 0.9
    assert phone.source_url == "https://example.com/contact"
    assert phone.raw_value == "Call us: 604-555-0100"
    assert phone.extraction_method == "regex"

    email = result.facts[1]
    assert email.field_name == "email"
    assert email.value == "info@example.com"
    assert email.confidence is None


def test_successful_no_match_response_is_not_an_error():
    handler = _json_handler(
        200,
        {"provider": "website_searxng", "matched": False, "facts": [], "error": None},
    )
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            result = WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert result.matched is False
    assert result.error is None
    assert result.facts == ()


# ---------------------------------------------------------------------------
# 11. No browser imports
# ---------------------------------------------------------------------------


def test_module_source_never_imports_browser_or_worker_packages():
    """Checks for an actual import STATEMENT, not a bare substring match
    -- the module's own docstring legitimately names crawl4ai/playwright/
    extruct/trafilatura in prose (explaining why they are absent), which
    a plain substring check would wrongly flag."""
    import ast

    import pipeline.company_enrichment.remote_provider as module

    source = open(module.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    for forbidden in ("crawl4ai", "playwright", "extruct", "trafilatura", "worker"):
        assert (
            forbidden not in imported_roots
        ), f"remote_provider.py imports {forbidden}"


def test_module_never_binds_a_name_from_a_browser_or_worker_package():
    """Checks remote_provider's own top-level namespace (not the global,
    process-wide sys.modules -- which other test files in the same
    pytest session legitimately populate with crawl4ai/playwright by
    importing WebsiteContactProvider, and would make this assertion
    flaky/false-positive depending on test run order) for any name whose
    defining module is one of the forbidden packages. Mirrors
    test_enrichment_worker.py's own
    test_worker_module_never_imports_db_connection pattern."""
    import pipeline.company_enrichment.remote_provider as module

    forbidden_prefixes = ("crawl4ai", "playwright", "extruct", "trafilatura", "worker")
    offending = [
        (name, getattr(value, "__module__", ""))
        for name, value in vars(module).items()
        if any(
            (getattr(value, "__module__", "") or "").startswith(p)
            for p in forbidden_prefixes
        )
    ]
    assert (
        offending == []
    ), f"remote_provider.py binds names from forbidden modules: {offending}"


# ---------------------------------------------------------------------------
# 12. No DB writes
# ---------------------------------------------------------------------------


def test_lookup_never_touches_the_session():
    session_mock = MagicMock(spec=Session)
    handler = _json_handler(
        200,
        {"provider": "website_searxng", "matched": False, "facts": [], "error": None},
    )
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            WebsiteContactRemoteProvider().lookup(session_mock, _request())
    assert session_mock.mock_calls == []


def test_lookup_never_touches_the_session_even_on_every_error_path():
    """The session must stay untouched on every branch, not just the
    happy path -- config errors, timeouts, and auth failures all return
    before (or without) ever needing it."""
    session_mock = MagicMock(spec=Session)

    with patch.dict(
        os.environ, _env(**{WORKER_URL_ENV_VAR: "https://evil.com"}), clear=False
    ):
        WebsiteContactRemoteProvider().lookup(session_mock, _request())
    assert session_mock.mock_calls == []

    handler = _raising_handler(httpx.ReadTimeout("simulated"))
    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            WebsiteContactRemoteProvider().lookup(session_mock, _request())
    assert session_mock.mock_calls == []


# ---------------------------------------------------------------------------
# 13. No retry -- exactly one attempt
# ---------------------------------------------------------------------------


def test_lookup_makes_exactly_one_attempt_never_retries():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ReadTimeout("simulated timeout")

    with patch.dict(os.environ, _env(), clear=False):
        with _mock_transport(handler):
            WebsiteContactRemoteProvider().lookup(MagicMock(), _request())
    assert call_count == 1


# ---------------------------------------------------------------------------
# 14. ENRICHMENT_ENABLED=false leaves the existing route untouched
# ---------------------------------------------------------------------------


def test_enrichment_route_still_disabled_by_default_remote_provider_not_reachable():
    """Adding remote_provider.py must not change the existing enrichment
    route's own gating -- it is still never wired into
    _default_providers(), so the route's behavior with
    ENRICHMENT_ENABLED unset/false must be byte-for-byte what it already
    was (mirrors test_internal_enrichment_route.py's own established
    pattern)."""
    from api import internal as internal_api

    request = MagicMock()
    request.headers.get.return_value = "secret"
    background_tasks = MagicMock()

    with patch.dict(
        os.environ,
        {"INTERNAL_API_KEY": "secret", "ENRICHMENT_ENABLED": "false"},
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


def test_default_providers_still_orgbook_only_remote_provider_not_wired():
    from pipeline.company_enrichment.orchestrator import _default_providers

    providers = _default_providers()
    provider_names = [p.name for p in providers]
    assert "website_searxng" not in provider_names
