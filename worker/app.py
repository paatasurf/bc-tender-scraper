"""Enrichment worker HTTP app
(docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S3.2, S4).

A single authenticated route (`POST /lookup`) that calls the EXISTING,
already-built-and-tested WebsiteContactProvider.lookup() and returns its
ProviderResult, unchanged, over HTTP. This module never re-implements
SSRF/DNS/robots/extraction logic (execution plan S4.9/item 4 of this
phase's authorization) -- every one of those checks still lives in, and
runs from, pipeline.company_enrichment.website_contact_provider exactly as
it does for the in-process caller today; this file is a thin HTTP
transport wrapper around that module, nothing more.

NOT wired into anything: this module is never imported by
pipeline/company_enrichment/orchestrator.py, api/internal.py, or
_default_providers(). It is not deployed, not added to railway.toml, and
this change does not start it running anywhere. Standing this app up as a
real Railway service is a separate, later, explicitly-authorized step
(execution plan S3.2/S13).

No database credentials, ever (S4.8): this module never imports
db.connection, never sets DATABASE_URL, never creates a SQLAlchemy engine.
_NoDatabaseSession below is a minimal stand-in for the Session argument
WebsiteContactProvider.lookup() requires -- see its own docstring for why
this is safe and already-covered by that function's existing, tested
company=None code path.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
import uuid
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pipeline.company_enrichment.provider import EnrichmentRequest, ProviderResult
from pipeline.company_enrichment.website_contact_provider import WebsiteContactProvider
from worker.auth import require_worker_api_key
from worker.models import FactModel, LookupRequest, LookupResponse

logger = logging.getLogger("enrichment_worker")

# Request/response size caps (S4.5). The request is just company_id/
# company_name/website/correlation_id -- never a large payload; 4KB is
# generous. The response is a handful of ProviderFacts, each a handful of
# short strings -- 64KB is generous headroom while still catching a
# genuinely anomalous response (e.g. an unexpectedly huge fact list)
# before it is ever sent, rather than silently truncating it.
MAX_REQUEST_BODY_BYTES = 4096
MAX_RESPONSE_BODY_BYTES = 64 * 1024

# Comfortably under orchestrator.DEFAULT_PROVIDER_TIMEOUT_S=90 (S4.6),
# leaving margin for the caller's own outer timeout to still observe a
# clean "timeout" outcome even if this deadline fires a few seconds late.
# A module-level name (not a function default) so tests can monkeypatch it
# without waiting out a real 60s deadline.
LOOKUP_TIMEOUT_S = 60.0

app = FastAPI(title="Company Enrichment Worker", docs_url=None, redoc_url=None)

_provider = WebsiteContactProvider()


class _NoDatabaseSession:
    """A minimal stand-in for a SQLAlchemy Session, passed to
    WebsiteContactProvider.lookup() so this worker never needs real DB
    credentials (S4.8/S6.1) -- the caller (a future
    WebsiteContactRemoteProvider, not built by this change) is responsible
    for resolving Company.website itself and always sending an
    already-resolved `website` value in the request body.

    WebsiteContactProvider.lookup() unconditionally calls
    session.get(Company, request.company_id) as its very first step; this
    stub returns None for that call every time. _resolve_domain() already
    handles a None company gracefully -- it falls through to
    request.website as the sole remaining candidate, exactly the same
    code path a company with no queryable row already exercises today
    (see test_website_contact_provider.py's own existing coverage of a
    None-company scenario). No network call, no DB call, nothing silently
    skipped -- this is the same logic, just never given a real session to
    query with."""

    def get(self, model: object, ident: object) -> object | None:  # noqa: D102
        # `-> object | None`, not a bare `-> None`: mypy treats a callable
        # annotated `-> None` as a procedure whose result must never be
        # used (func-returns-value), which would make every caller that
        # inspects this method's result (this stub always returns None,
        # but callers -- including this file's own tests -- legitimately
        # compare that result) a type error. `object | None` states the
        # true contract (a value is returned, and it is always None here)
        # without widening to `Any`.
        return None


def _run_lookup_with_timeout(
    request: EnrichmentRequest, timeout_s: float
) -> tuple[ProviderResult | None, str]:
    """Thread + wall-clock deadline, deliberately mirroring the SHAPE of
    orchestrator._call_provider_with_timeout()'s pattern without importing
    orchestrator.py itself -- that module pulls in
    db.company_enrichment_tables (SQLAlchemy Core objects tied to a real
    DB schema) and other orchestrator-only machinery this worker must
    never depend on (S4.8: no DB credentials, and this worker has no
    concept of jobs/leases/dedup at all -- S3.2). This is a generic
    "don't let one call hang the request forever" utility, not a
    duplication of any SSRF/extraction/orchestration logic.

    WebsiteContactProvider.lookup() should never actually raise (its own
    module docstring: "no match is a valid outcome, never an error") --
    the `except Exception` branch below is this worker's own
    defense-in-depth net for a genuinely unexpected exception, never
    relied upon as the normal path.

    Returns (result_or_None, tag) where tag is exactly one of
    "ok" | "error" | "timeout"."""

    def _call() -> ProviderResult:
        # cast(), not a structural-subtyping trick: _NoDatabaseSession
        # deliberately does NOT claim to be a real sqlalchemy.orm.Session
        # (S4.8's whole point) -- it only implements the one method
        # lookup() actually calls, .get(). This is an intentional,
        # documented type substitution at the one call site that needs
        # it, not a general-purpose Session replacement.
        return _provider.lookup(cast(Session, _NoDatabaseSession()), request)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call)
    try:
        result = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False)
        return None, "timeout"
    except Exception:  # noqa: BLE001 -- see docstring: defense-in-depth only
        executor.shutdown(wait=False)
        return None, "error"
    executor.shutdown(wait=False)
    return result, "ok"


@app.get("/health")
def health() -> dict[str, str]:
    """Unauthenticated liveness only (S4.3) -- no DB access, no fetch
    capability, no company data reachable from this route. Railway's own
    deploy healthcheck prober cannot supply the shared-secret header, so
    this one route is the deliberate, narrow exception to S4.2's auth
    requirement, and it does nothing else."""
    return {"status": "ok"}


@app.post("/lookup", response_model=LookupResponse)
async def lookup(
    request: Request, _auth: None = Depends(require_worker_api_key)
) -> LookupResponse:
    correlation_id: str | None = None
    started = time.monotonic()
    try:
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            logger.warning(
                "enrichment_worker: request rejected, oversized body (%d bytes)",
                len(body),
            )
            raise HTTPException(status_code=413, detail="Request body too large")

        try:
            payload = LookupRequest.model_validate_json(body)
        except ValidationError as exc:
            logger.warning("enrichment_worker: request rejected, malformed: %s", exc)
            raise HTTPException(status_code=422, detail="Malformed request") from exc

        correlation_id = payload.correlation_id or str(uuid.uuid4())
        logger.info(
            "enrichment_worker: lookup started correlation_id=%s company_id=%s",
            correlation_id,
            payload.company_id,
        )

        enrichment_request = EnrichmentRequest(
            company_id=payload.company_id,
            company_name=payload.company_name,
            website=payload.website,
        )
        # Confirmed empirically (real uvicorn + concurrent HTTP requests,
        # independent review round): calling _run_lookup_with_timeout()
        # directly here -- a blocking call, since it itself blocks on
        # concurrent.futures.Future.result(timeout=...) -- executes on
        # THIS coroutine's own event-loop thread, since `lookup` is
        # `async def` and FastAPI never auto-offloads an async route to a
        # threadpool (that behavior is `def`-routes only). That blocked
        # the whole event loop for the call's duration, including
        # /health -- a concurrent /health request measured ~1.7s of the
        # blocking call's ~2s duration instead of returning immediately.
        # `asyncio.to_thread()` runs the ENTIRE call (including its own
        # internal ThreadPoolExecutor + timeout wait) on a separate
        # worker thread, freeing this coroutine to suspend/await instead
        # of blocking -- /health and any other concurrent request can now
        # be served while a /lookup call is in flight. This changes only
        # WHERE _run_lookup_with_timeout() runs, not what it does: the
        # function itself, its timeout value, its no-retry behavior, and
        # its "ok"/"error"/"timeout" tags are all unchanged.
        result, tag = await asyncio.to_thread(
            _run_lookup_with_timeout, enrichment_request, LOOKUP_TIMEOUT_S
        )

        if tag == "timeout":
            response = LookupResponse(
                provider=_provider.name,
                matched=False,
                error=f"timeout:worker_lookup_exceeded_{LOOKUP_TIMEOUT_S:.0f}s",
                correlation_id=correlation_id,
            )
        elif tag == "error" or result is None:
            response = LookupResponse(
                provider=_provider.name,
                matched=False,
                error="worker_internal_error:unexpected_exception",
                correlation_id=correlation_id,
            )
        else:
            response = LookupResponse(
                provider=result.provider,
                matched=result.matched,
                facts=[
                    FactModel(
                        field_name=f.field_name,
                        value=f.value,
                        confidence=f.confidence,
                        source_url=f.source_url,
                        raw_value=f.raw_value,
                        extraction_method=f.extraction_method,
                    )
                    for f in result.facts
                ],
                error=result.error,
                correlation_id=correlation_id,
            )

        body_out = response.model_dump_json().encode("utf-8")
        if len(body_out) > MAX_RESPONSE_BODY_BYTES:
            # Fail loud, never silently truncate a response -- matches
            # this project's established "fail-explicit, never repair or
            # hide silently" convention. Should never happen in practice
            # (a handful of ProviderFacts is inherently small); if it does,
            # something upstream produced an anomaly worth surfacing, not
            # papering over with a cut-off JSON body.
            logger.error(
                "enrichment_worker: response exceeded size cap, "
                "correlation_id=%s (%d bytes)",
                correlation_id,
                len(body_out),
            )
            raise HTTPException(status_code=500, detail="Response too large")

        elapsed = time.monotonic() - started
        logger.info(
            "enrichment_worker: lookup finished correlation_id=%s matched=%s "
            "error=%s elapsed_s=%.2f",
            correlation_id,
            response.matched,
            response.error,
            elapsed,
        )
        return response
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 -- last-resort net, never leaks details
        logger.exception(
            "enrichment_worker: unhandled exception, correlation_id=%s",
            correlation_id,
        )
        raise HTTPException(status_code=500, detail="Internal error") from None
