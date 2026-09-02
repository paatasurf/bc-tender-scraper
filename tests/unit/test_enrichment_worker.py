"""Unit tests for the enrichment worker HTTP contract
(docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S3.2, S4).

Mock-based throughout for the network-touching cases (matching
test_website_contact_provider.py's own established convention -- no real
external network call anywhere in this file). Uses `fastapi.testclient.TestClient`
(matching test_internal_enrichment_route.py's own precedent) with
`raise_server_exceptions=False` so a 500-path test can assert on the HTTP
response instead of an in-process Python exception.

One deliberate exception: the event-loop-blocking regression tests near
the bottom of this file start a REAL `uvicorn.Server` bound to
127.0.0.1 on an OS-assigned free port and issue REAL concurrent HTTP
requests via `httpx` from separate threads. This is necessary because
`TestClient`'s own transport/threading model does not reproduce a
shared-event-loop blocking bug (confirmed directly: a TestClient-based
probe showed no blocking at all where a real uvicorn server did). Every
request in those tests targets 127.0.0.1 only -- no external network
call, no production dependency.

This worker is never imported by, and never imports, orchestrator.py or
api/internal.py -- these tests exercise worker/app.py entirely in
isolation, proving the contract stands on its own, not that it is wired
into anything (it structurally is not, and this phase does not wire it).
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pipeline.company_enrichment.provider import ProviderFact, ProviderResult
from worker.app import MAX_REQUEST_BODY_BYTES, _NoDatabaseSession, app
from worker.auth import WORKER_AUTH_ENV_VAR, WORKER_AUTH_HEADER

PROVIDER_MODULE = "pipeline.company_enrichment.website_contact_provider"
WORKER_SECRET = "test-worker-secret-do-not-use-in-production"


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _worker_secret_env():
    with patch.dict(os.environ, {WORKER_AUTH_ENV_VAR: WORKER_SECRET}):
        yield


def _auth_headers() -> dict[str, str]:
    return {WORKER_AUTH_HEADER: WORKER_SECRET}


def _valid_body(**overrides) -> dict:
    body = {
        "company_id": 1,
        "company_name": "Acme Construction Ltd",
        "website": "example.com",
        "correlation_id": "11111111-1111-1111-1111-111111111111",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


def test_healthcheck_requires_no_auth(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthcheck_reveals_nothing_beyond_liveness(client) -> None:
    response = client.get("/health")
    assert set(response.json().keys()) == {"status"}


# ---------------------------------------------------------------------------
# Valid request
# ---------------------------------------------------------------------------


def test_valid_request_returns_matched_result(client) -> None:
    fact = ProviderFact(
        field_name="phone",
        value="6045551234",
        confidence=0.85,
        source_url="https://example.com/contact",
        raw_value="(604) 555-1234",
        extraction_method="json_ld",
    )
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(
            provider="website_contact", matched=True, facts=(fact,)
        ),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is True
    assert payload["provider"] == "website_contact"
    assert payload["error"] is None
    assert payload["correlation_id"] == "11111111-1111-1111-1111-111111111111"
    assert len(payload["facts"]) == 1
    assert payload["facts"][0]["field_name"] == "phone"
    assert payload["facts"][0]["value"] == "6045551234"
    assert payload["facts"][0]["extraction_method"] == "json_ld"


def test_valid_request_without_correlation_id_gets_one_generated(client) -> None:
    body = _valid_body()
    del body["correlation_id"]
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(provider="website_contact", matched=False),
    ):
        response = client.post("/lookup", json=body, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["correlation_id"] is not None
    assert response.json()["correlation_id"] != ""


def test_clean_no_match_is_a_normal_200_not_an_error(client) -> None:
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(provider="website_contact", matched=False),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert payload["error"] is None
    assert payload["facts"] == []


# ---------------------------------------------------------------------------
# Malformed request
# ---------------------------------------------------------------------------


def test_missing_required_field_is_rejected(client) -> None:
    body = _valid_body()
    del body["company_name"]
    response = client.post("/lookup", json=body, headers=_auth_headers())
    assert response.status_code == 422


def test_wrong_type_for_company_id_is_rejected(client) -> None:
    body = _valid_body(company_id="not-an-int")
    response = client.post("/lookup", json=body, headers=_auth_headers())
    assert response.status_code == 422


def test_non_positive_company_id_is_rejected(client) -> None:
    body = _valid_body(company_id=0)
    response = client.post("/lookup", json=body, headers=_auth_headers())
    assert response.status_code == 422


def test_unknown_extra_field_is_rejected_strict_validation(client) -> None:
    body = _valid_body(unexpected_field="should not be allowed")
    response = client.post("/lookup", json=body, headers=_auth_headers())
    assert response.status_code == 422


def test_not_even_valid_json_is_rejected(client) -> None:
    response = client.post(
        "/lookup",
        content=b"{not valid json at all",
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_overlong_company_name_is_rejected(client) -> None:
    body = _valid_body(company_name="x" * 301)
    response = client.post("/lookup", json=body, headers=_auth_headers())
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Missing / invalid auth
# ---------------------------------------------------------------------------


def test_missing_auth_header_is_rejected(client) -> None:
    response = client.post("/lookup", json=_valid_body())
    assert response.status_code == 403


def test_wrong_auth_key_is_rejected(client) -> None:
    response = client.post(
        "/lookup",
        json=_valid_body(),
        headers={WORKER_AUTH_HEADER: "totally-wrong-key"},
    )
    assert response.status_code == 403


def test_empty_auth_key_is_rejected(client) -> None:
    response = client.post(
        "/lookup", json=_valid_body(), headers={WORKER_AUTH_HEADER: ""}
    )
    assert response.status_code == 403


def test_server_secret_unset_rejects_every_request_fail_closed(client) -> None:
    """Never a default/empty-string bypass -- an unconfigured server
    secret means every request is refused, matching
    _require_internal_key()'s own fail-closed precedent."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(WORKER_AUTH_ENV_VAR, None)
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())
    assert response.status_code == 403


def test_auth_uses_constant_time_comparison() -> None:
    """hmac.compare_digest() is used, not `==` -- confirmed by reading the
    actual call, not just trusting the docstring (this project's own
    established verification standard)."""
    import inspect

    from worker import auth as auth_module

    source = inspect.getsource(auth_module.require_worker_api_key)
    assert "hmac.compare_digest" in source
    assert " key == expected" not in source and "expected == key" not in source


# ---------------------------------------------------------------------------
# Oversized request / response
# ---------------------------------------------------------------------------


def test_oversized_request_body_is_rejected(client) -> None:
    body = _valid_body(company_name="x" * MAX_REQUEST_BODY_BYTES)
    raw = json.dumps(body).encode("utf-8")
    assert len(raw) > MAX_REQUEST_BODY_BYTES
    response = client.post(
        "/lookup",
        content=raw,
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )
    # Either the strict company_name length cap (422) or the raw body-size
    # cap (413) rejects this -- both are correct outcomes for an oversized
    # payload; the load-bearing property is that it is never accepted.
    assert response.status_code in (413, 422)


def test_request_body_size_cap_alone_triggers_413_before_field_parsing() -> None:
    """A request whose RAW body exceeds the byte cap must be rejected on
    size alone, even if -- hypothetically -- its fields would otherwise
    have been individually valid-shaped; proven by constructing a body
    that is oversized only via a field with no length cap of its own
    (there isn't one here, so this asserts the byte-length gate fires
    strictly before Pydantic validation ever runs, using a monkeypatched,
    tiny cap to keep the test fast and deterministic)."""
    with patch("worker.app.MAX_REQUEST_BODY_BYTES", 32):
        client_local = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, {WORKER_AUTH_ENV_VAR: WORKER_SECRET}):
            response = client_local.post(
                "/lookup", json=_valid_body(), headers=_auth_headers()
            )
    assert response.status_code == 413


def test_oversized_response_is_rejected_not_silently_truncated(client) -> None:
    huge_facts = tuple(
        ProviderFact(
            field_name="phone",
            value=str(i).zfill(10),
            confidence=0.5,
            source_url="https://example.com/contact",
            raw_value="x" * 2000,
            extraction_method="trafilatura_text",
        )
        for i in range(50)
    )
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(
            provider="website_contact", matched=True, facts=huge_facts
        ),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())
    assert response.status_code == 500
    # Never a partial/truncated JSON body standing in for the real one.
    assert "too large" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_lookup_timeout_returns_200_with_a_timeout_error_tag(client) -> None:
    import time

    def _slow_lookup(session, request):  # noqa: ANN001, ARG001
        time.sleep(1.0)
        return ProviderResult(provider="website_contact", matched=False)

    with (
        patch("worker.app.LOOKUP_TIMEOUT_S", 0.05),
        patch("worker.app._provider.lookup", side_effect=_slow_lookup),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert payload["error"] is not None
    assert payload["error"].startswith("timeout:")


def test_lookup_timeout_does_not_wait_for_the_slow_call() -> None:
    """The HTTP response returns close to the (short, patched) timeout,
    not close to the slow call's real duration -- proves the deadline is
    actually enforced, not just that the tag says "timeout" while the
    request secretly blocked the full duration anyway."""
    import time

    def _slow_lookup(session, request):  # noqa: ANN001, ARG001
        time.sleep(2.0)
        return ProviderResult(provider="website_contact", matched=False)

    with (
        patch("worker.app.LOOKUP_TIMEOUT_S", 0.05),
        patch("worker.app._provider.lookup", side_effect=_slow_lookup),
        patch.dict(os.environ, {WORKER_AUTH_ENV_VAR: WORKER_SECRET}),
    ):
        client_local = TestClient(app, raise_server_exceptions=False)
        started = time.monotonic()
        response = client_local.post(
            "/lookup", json=_valid_body(), headers=_auth_headers()
        )
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 1.0  # well under the 2s the slow call actually takes


# ---------------------------------------------------------------------------
# Provider error (generic passthrough)
# ---------------------------------------------------------------------------


def test_provider_error_is_passed_through_as_a_normal_200(client) -> None:
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(
            provider="website_contact",
            matched=False,
            error="robots_disallowed:Disallow: /",
        ),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert payload["error"] == "robots_disallowed:Disallow: /"


def test_unexpected_provider_exception_is_isolated_never_raw_leaked(client) -> None:
    """WebsiteContactProvider.lookup() should never raise, but this
    worker's own defense-in-depth net must still isolate it cleanly --
    matching _call_provider_with_timeout()'s own "provider errors are
    isolated" precedent -- and never leak the raw exception text (which
    could carry sensitive detail, matching this session's own db_error
    leak-prevention precedent for a different function)."""
    with patch(
        "worker.app._provider.lookup",
        side_effect=RuntimeError("simulated unexpected internal failure"),
    ):
        response = client.post("/lookup", json=_valid_body(), headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert payload["error"] == "worker_internal_error:unexpected_exception"
    assert "simulated unexpected internal failure" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# SSRF rejection (passthrough, not re-implemented)
# ---------------------------------------------------------------------------


def test_ssrf_rejection_is_passed_through_unmodified(client) -> None:
    """This worker never re-implements the SSRF guard -- it calls the
    real WebsiteContactProvider.lookup(), which already runs
    _ssrf_and_dns_check() internally. This test only proves the worker's
    OWN transport layer doesn't lose or reshape that outcome; the guard's
    own behavior is test_website_contact_provider.py's job, not
    duplicated here."""
    with patch(
        "worker.app._provider.lookup",
        return_value=ProviderResult(
            provider="website_contact",
            matched=False,
            error="ssrf_blocked:169.254.169.254 resolves inside blocked range",
        ),
    ):
        response = client.post(
            "/lookup",
            json=_valid_body(website="169.254.169.254.example-attacker.test"),
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert payload["error"].startswith("ssrf_blocked:")


# ---------------------------------------------------------------------------
# No DB access -- end-to-end, real WebsiteContactProvider.lookup(), only
# the network-touching module functions mocked (matching
# test_website_contact_provider.py's own established mocking boundary).
# ---------------------------------------------------------------------------


def test_no_db_access_end_to_end_real_lookup_via_request_website_only(client) -> None:
    """Proves the S4.8 architecture actually works, not just that it's
    documented: a REAL WebsiteContactProvider.lookup() call (not mocked)
    runs to completion using ONLY request.website -- _NoDatabaseSession.get()
    returns None (no database, no credentials, nothing to query), and the
    domain resolution still succeeds via the request-supplied candidate,
    exactly as the execution plan's S4.8/S6.1 architecture requires."""
    html = (
        "<html><body>"
        '<script type="application/ld+json">'
        '{"@context": "https://schema.org", "@type": "Organization", '
        '"name": "Acme Construction Ltd", "telephone": "604-555-1234"}'
        "</script>"
        "</body></html>"
    )
    with (
        patch(f"{PROVIDER_MODULE}._check_robots", return_value={"allowed": True}),
        patch(
            f"{PROVIDER_MODULE}._redirect_walk",
            return_value={
                "outcome": "resolved",
                "final_url": "https://example.com/",
                "final_status": 200,
                "hops": [],
            },
        ),
        patch(f"{PROVIDER_MODULE}._ssrf_and_dns_check"),
        patch(
            f"{PROVIDER_MODULE}._fetch_rendered_page",
            new_callable=AsyncMock,
            return_value=(html, 200, "https://example.com/"),
        ),
    ):
        response = client.post(
            "/lookup",
            json=_valid_body(website="example.com"),
            headers=_auth_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is True
    assert any(f["field_name"] == "phone" for f in payload["facts"])


def test_no_db_session_get_never_raises_and_always_returns_none() -> None:
    stub = _NoDatabaseSession()
    assert stub.get(object(), 12345) is None
    assert stub.get(object(), None) is None


def test_no_database_session_has_no_engine_or_connection_attribute() -> None:
    """Structural proof this is not a real Session wearing a disguise --
    it has none of SQLAlchemy Session's own connection-bearing attributes."""
    stub = _NoDatabaseSession()
    assert not hasattr(stub, "bind")
    assert not hasattr(stub, "connection")
    assert not hasattr(stub, "execute")


def test_worker_module_never_imports_db_connection() -> None:
    """Static proof (not just runtime behavior) that this worker never
    references db.connection -- the module that would actually resolve
    DATABASE_URL and create a real engine."""
    import worker.app as app_module

    assert not hasattr(app_module, "get_session")
    assert not hasattr(app_module, "get_engine")
    assert "db.connection" not in {
        getattr(v, "__module__", "") for v in vars(app_module).values()
    }


# ---------------------------------------------------------------------------
# Event-loop non-blocking regression -- REAL uvicorn, REAL concurrent HTTP
# requests (not fastapi.testclient.TestClient, whose own httpx-transport
# threading model does NOT reproduce a shared-event-loop blocking bug --
# confirmed directly during the independent review that found this: a
# TestClient-based probe showed no blocking at all, a real uvicorn server
# hit with two genuinely concurrent HTTP requests showed /health taking
# ~1.7s of a ~2s concurrent /lookup call's duration before the fix below).
# Every request here targets 127.0.0.1 only, on a dynamically-assigned
# free port -- no external network call, no production dependency.
# ---------------------------------------------------------------------------


@pytest.fixture
def running_worker_server():
    """Starts worker.app.app under a REAL uvicorn.Server, in a background
    thread, bound to 127.0.0.1 on an OS-assigned free port (never a fixed
    port, to avoid CI port collisions). Yields the base URL
    ("http://127.0.0.1:<port>"); shuts the server down cleanly afterward."""
    import threading

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn server did not start in time"

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def test_slow_lookup_does_not_block_concurrent_health_real_server(
    running_worker_server,
) -> None:
    """The regression test for the event-loop-blocking finding: a slow
    /lookup (mocked provider sleeping SLOW_LOOKUP_S) running concurrently
    with a /health request -- /health must respond independently and
    quickly (well under SLOW_LOOKUP_S), not wait for /lookup to finish.
    Before the asyncio.to_thread() fix, this same test measured /health
    taking ~1.7s (nearly the full blocking duration); after the fix it
    consistently returns in well under 0.5s."""
    import threading

    import httpx

    SLOW_LOOKUP_S = 2.0
    HEALTH_MAX_ACCEPTABLE_S = 1.0

    def _slow_lookup(session, request):  # noqa: ANN001, ARG001
        time.sleep(SLOW_LOOKUP_S)
        return ProviderResult(provider="website_contact", matched=False)

    lookup_status: int = 0
    lookup_elapsed: float = 0.0
    health_status: int = 0
    health_elapsed: float = 0.0

    def _do_lookup() -> None:
        nonlocal lookup_status, lookup_elapsed
        started = time.monotonic()
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{running_worker_server}/lookup",
                json=_valid_body(),
                headers=_auth_headers(),
            )
        lookup_status = resp.status_code
        lookup_elapsed = time.monotonic() - started

    def _do_health() -> None:
        nonlocal health_status, health_elapsed
        time.sleep(0.3)  # ensure this overlaps with the slow /lookup above
        started = time.monotonic()
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{running_worker_server}/health")
        health_status = resp.status_code
        health_elapsed = time.monotonic() - started

    with patch("worker.app._provider.lookup", side_effect=_slow_lookup):
        lookup_thread = threading.Thread(target=_do_lookup)
        health_thread = threading.Thread(target=_do_health)
        lookup_thread.start()
        health_thread.start()
        lookup_thread.join(timeout=10.0)
        health_thread.join(timeout=10.0)

    assert lookup_status == 200
    assert health_status == 200
    assert health_elapsed < HEALTH_MAX_ACCEPTABLE_S, (
        "concurrent /health was blocked by the slow /lookup -- "
        f"took {health_elapsed:.2f}s, expected well under "
        f"{HEALTH_MAX_ACCEPTABLE_S}s (event loop is blocked again)"
    )
    # The slow /lookup itself still takes roughly its full sleep duration
    # (proving the fix didn't accidentally short-circuit or skip it).
    assert lookup_elapsed >= SLOW_LOOKUP_S * 0.9


def test_two_concurrent_slow_lookups_both_complete_independently(
    running_worker_server,
) -> None:
    """A second, complementary proof: two concurrent /lookup calls (not
    just /lookup + /health) both complete close to their own individual
    sleep duration, not serialized end-to-end one after the other --
    confirming asyncio.to_thread() actually parallelizes the blocking
    work rather than just moving where a single serialization point sits."""
    import threading

    import httpx

    SLOW_LOOKUP_S = 1.0

    def _slow_lookup(session, request):  # noqa: ANN001, ARG001
        time.sleep(SLOW_LOOKUP_S)
        return ProviderResult(provider="website_contact", matched=False)

    elapsed: dict[str, float] = {}

    def _do_lookup(key: str) -> None:
        started = time.monotonic()
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{running_worker_server}/lookup",
                json=_valid_body(),
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        elapsed[key] = time.monotonic() - started

    with patch("worker.app._provider.lookup", side_effect=_slow_lookup):
        overall_started = time.monotonic()
        t1 = threading.Thread(target=_do_lookup, args=("a",))
        t2 = threading.Thread(target=_do_lookup, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        overall_elapsed = time.monotonic() - overall_started

    # Serialized (blocking) execution would take ~2x SLOW_LOOKUP_S overall;
    # concurrent execution takes ~1x SLOW_LOOKUP_S overall.
    assert overall_elapsed < SLOW_LOOKUP_S * 1.8, (
        f"two concurrent /lookup calls took {overall_elapsed:.2f}s combined "
        f"-- expected close to {SLOW_LOOKUP_S}s (parallel), not "
        f"~{SLOW_LOOKUP_S * 2}s (serialized)"
    )
