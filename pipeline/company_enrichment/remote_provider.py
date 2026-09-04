"""Remote HTTP adapter for the company-enrichment worker service
(Phase 3E: docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S3.1, S4).

`WebsiteContactRemoteProvider` implements the existing `EnrichmentProvider`
Protocol (pipeline.company_enrichment.provider) as a thin HTTP client
around the separate worker service (worker/app.py). It never imports
crawl4ai, playwright, extruct, or trafilatura -- those heavy,
browser-launching dependencies stay entirely out of the main API's own
Nixpacks build. The only network dependency used here is `httpx`, already
a main-API dependency (requirements.txt) for other purposes.

Deliberately does NOT import anything from the `worker` package (models,
auth, or otherwise) -- worker/__init__.py's own docstring states the
worker is "NOT imported by, and never imported from, api/internal.py or
pipeline/company_enrichment/orchestrator.py"; importing worker.models
here would quietly violate that same boundary from the other direction.
Instead, this module defines its own local, structurally-mirrored
response models (`_RemoteFact`, `_RemoteLookupResponse` below) -- close
enough to worker/models.py's `FactModel`/`LookupResponse` to validate the
wire contract, intentionally NOT the same Python objects, so the two
services can still evolve independently and neither ever needs the
other's source tree importable at runtime.

NOT wired into anything by this phase:
`pipeline.company_enrichment.orchestrator._default_providers()` still
returns `(OrgBookAdapter(),)` only (untouched, not modified by this
file). This class is built, tested, and importable, but reachable only
by direct instantiation until a separate, later, explicitly-authorized
wiring step (execution plan S8 stage 7).

Known limitation, documented rather than worked around: `EnrichmentRequest`
(pipeline.company_enrichment.provider) has no `run_id`/`correlation_id`
field, and `EnrichmentProvider.lookup()`'s fixed signature --
`lookup(self, session, request)` -- gives this provider no way to receive
the actual `company_enrichment_jobs.run_id` an orchestrator-driven call is
part of, without changing either `provider.py`'s `EnrichmentRequest` or
`orchestrator.py` itself (both out of this phase's scope: "не менять
orchestrator"). This provider generates its own per-call `correlation_id`
(a fresh UUID4) instead -- it still lets a single request's logs be
grepped consistently across both services, just not tied to the job's own
`run_id`. True end-to-end correlation is a follow-up change, not
implemented here.
"""

from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from pipeline.company_enrichment.provider import (
    EnrichmentRequest,
    ProviderFact,
    ProviderResult,
)

logger = logging.getLogger(__name__)

WORKER_URL_ENV_VAR = "ENRICHMENT_WORKER_URL"
WORKER_API_KEY_ENV_VAR = "ENRICHMENT_WORKER_API_KEY"
WORKER_AUTH_HEADER = "X-Enrichment-Worker-Key"
CORRELATION_ID_HEADER = "X-Correlation-Id"

# Comfortably under orchestrator.DEFAULT_PROVIDER_TIMEOUT_S=90 (execution
# plan S4.6) -- leaves margin for _call_provider_with_timeout()'s own
# thread-based wrapper to still record a clean "timeout" tag even if
# this HTTP client's own timeout fires a few seconds late. This IS this
# provider's own required network-level timeout (Protocol docstring:
# "MANDATORY for any implementation that performs network I/O").
REQUEST_TIMEOUT_S = 60.0

# Mirrors worker/app.py's own MAX_RESPONSE_BODY_BYTES -- defense in
# depth on THIS side too, enforced by streaming the response and
# aborting mid-read, not just trusting a Content-Length header (which a
# misbehaving or compromised worker could omit or misreport).
MAX_RESPONSE_BYTES = 64 * 1024

# execution plan S4.1: reachable ONLY over Railway's private internal
# network. This is a hard allow-list -- not a denylist of known-bad
# hosts -- so anything that doesn't end in this suffix is rejected,
# including public Railway domains (*.rlwy.net, *.up.railway.app),
# localhost, bare IP literals, and any other host.
_INTERNAL_HOST_SUFFIX = ".railway.internal"

_PROVIDER_NAME = (
    "website_searxng"  # matches the decision doc's established `source` value
)


class _WorkerConfigError(Exception):
    """Internal-only: a missing env var or an invalid/non-internal
    ENRICHMENT_WORKER_URL. Always caught inside lookup() and converted
    to ProviderResult(error=...) -- never escapes to the caller (Protocol
    contract: lookup() must never raise). Its message never includes the
    API key's value -- only whether it is present."""


class _ResponseTooLargeError(Exception):
    """Internal-only: the worker's response exceeded MAX_RESPONSE_BYTES
    while streaming. Always caught inside lookup()."""


class _RemoteFact(BaseModel):
    """Local mirror of worker/models.py's FactModel -- deliberately a
    separate class, not an import of that one (see module docstring)."""

    model_config = ConfigDict(extra="ignore")

    field_name: str
    value: str
    confidence: float | None = None
    source_url: str | None = None
    raw_value: str | None = None
    extraction_method: str | None = None


class _RemoteLookupResponse(BaseModel):
    """Local mirror of worker/models.py's LookupResponse."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    matched: bool
    facts: list[_RemoteFact] = Field(default_factory=list)
    error: str | None = None
    correlation_id: str | None = None


def _resolve_worker_config() -> tuple[str, str]:
    """Reads ENRICHMENT_WORKER_URL and ENRICHMENT_WORKER_API_KEY from the
    environment ONLY (S4.2) -- never hardcoded, never read from any other
    source. Raises _WorkerConfigError (always caught by lookup()) if
    either is unset/blank or the URL fails validation."""
    worker_url = os.environ.get(WORKER_URL_ENV_VAR, "").strip()
    api_key = os.environ.get(WORKER_API_KEY_ENV_VAR, "").strip()

    if not worker_url:
        raise _WorkerConfigError(f"{WORKER_URL_ENV_VAR} is not set")
    if not api_key:
        raise _WorkerConfigError(f"{WORKER_API_KEY_ENV_VAR} is not set")

    _validate_internal_worker_url(worker_url)
    return worker_url, api_key


def _validate_internal_worker_url(worker_url: str) -> None:
    """Hard allow-list (execution plan S4.1). Never logs or echoes
    worker_url's credentials in any raised message -- there should never
    be any (embedded userinfo is itself rejected below), but this
    function still never interpolates the raw URL into its error
    messages, only the env var name, to avoid ever accidentally logging
    something an operator pasted a secret into by mistake."""
    parts = urlsplit(worker_url)

    if parts.scheme not in ("http", "https"):
        raise _WorkerConfigError(
            f"{WORKER_URL_ENV_VAR} has an unsupported scheme " "(must be http or https)"
        )
    if parts.username or parts.password:
        raise _WorkerConfigError(
            f"{WORKER_URL_ENV_VAR} must not contain embedded credentials"
        )
    hostname = (parts.hostname or "").lower()
    if not hostname.endswith(_INTERNAL_HOST_SUFFIX):
        raise _WorkerConfigError(
            f"{WORKER_URL_ENV_VAR} must resolve to an internal "
            f"*{_INTERNAL_HOST_SUFFIX} hostname, not a public endpoint"
        )


def _read_capped(response: httpx.Response, limit: int) -> bytes:
    """Streams the response body, aborting with _ResponseTooLargeError
    the instant more than `limit` bytes have been received -- enforced
    by actually reading in chunks, not by trusting a Content-Length
    header the server could omit or misreport."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise _ResponseTooLargeError(f"response exceeded {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


class WebsiteContactRemoteProvider:
    """HTTP adapter for the separate company-enrichment worker service.
    Structurally implements EnrichmentProvider (duck-typed, matching this
    repo's existing provider convention -- OrgBookAdapter is not a formal
    subclass of the Protocol either)."""

    name = _PROVIDER_NAME
    is_fact_source = True

    def lookup(self, session: Session, request: EnrichmentRequest) -> ProviderResult:
        # Never touches the database (S4.8) -- `session` is part of the
        # Protocol's fixed signature but unused here. Company.website
        # resolution already happened one layer up, wherever `request`
        # was constructed; `request.website` is used exactly as given.
        del session

        correlation_id = str(uuid.uuid4())

        try:
            worker_url, api_key = _resolve_worker_config()
        except _WorkerConfigError as exc:
            logger.warning(
                "[remote_provider] configuration error, correlation_id=%s: %s",
                correlation_id,
                exc,
            )
            return ProviderResult(
                provider=self.name, matched=False, error=f"config_error:{exc}"
            )

        payload = {
            "company_id": request.company_id,
            "company_name": request.company_name,
            "website": request.website,
            "correlation_id": correlation_id,
        }
        headers = {
            WORKER_AUTH_HEADER: api_key,
            CORRELATION_ID_HEADER: correlation_id,
            "Content-Type": "application/json",
        }
        lookup_url = worker_url.rstrip("/") + "/lookup"

        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client, client.stream(
                "POST", lookup_url, json=payload, headers=headers
            ) as response:
                status_code = response.status_code
                body = _read_capped(response, MAX_RESPONSE_BYTES)
        except httpx.TimeoutException:
            logger.warning(
                "[remote_provider] worker timeout, correlation_id=%s", correlation_id
            )
            return ProviderResult(
                provider=self.name,
                matched=False,
                error=f"timeout:worker_lookup_exceeded_{REQUEST_TIMEOUT_S:.0f}s",
            )
        except _ResponseTooLargeError:
            logger.warning(
                "[remote_provider] worker response too large, correlation_id=%s",
                correlation_id,
            )
            return ProviderResult(
                provider=self.name,
                matched=False,
                error="malformed_response:oversized",
            )
        except httpx.HTTPError as exc:
            # Network-level failure (connection refused/reset, DNS
            # failure, etc.) -- the worker itself is unreachable, not
            # just slow. Never includes api_key; httpx exceptions never
            # carry outbound header values in their string form.
            logger.warning(
                "[remote_provider] worker unavailable, correlation_id=%s: %s",
                correlation_id,
                type(exc).__name__,
            )
            return ProviderResult(
                provider=self.name,
                matched=False,
                error=f"worker_unavailable:{type(exc).__name__}",
            )

        if status_code in (401, 403):
            return ProviderResult(
                provider=self.name,
                matched=False,
                error=f"auth_error:http_{status_code}",
            )
        if status_code != 200:
            # Covers 5xx (worker crashed/overloaded) and any other
            # unexpected status this taxonomy has no more specific
            # bucket for -- the worker is not behaving as a healthy
            # /lookup endpoint should, regardless of the exact code.
            return ProviderResult(
                provider=self.name,
                matched=False,
                error=f"worker_unavailable:http_{status_code}",
            )

        try:
            parsed = _RemoteLookupResponse.model_validate_json(body)
        except ValidationError as exc:
            logger.warning(
                "[remote_provider] malformed worker response, correlation_id=%s: %s",
                correlation_id,
                type(exc).__name__,
            )
            return ProviderResult(
                provider=self.name,
                matched=False,
                error=f"malformed_response:{type(exc).__name__}",
            )

        # Successful round trip -- preserve every fact and metadata field
        # losslessly (requirement: no data dropped on the happy path).
        return ProviderResult(
            provider=parsed.provider,
            matched=parsed.matched,
            facts=tuple(
                ProviderFact(
                    field_name=f.field_name,
                    value=f.value,
                    confidence=f.confidence,
                    source_url=f.source_url,
                    raw_value=f.raw_value,
                    extraction_method=f.extraction_method,
                )
                for f in parsed.facts
            ),
            error=parsed.error,
        )
