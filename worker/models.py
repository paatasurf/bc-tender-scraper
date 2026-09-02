"""Request/response models for the enrichment worker's HTTP contract
(docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md S3.1/S3.2, S4).

Deliberately thin: structural validation only (types, required fields,
length caps) lives here. Semantic validation of the `website` field itself
-- is this shaped like a real domain, is it a sentinel/placeholder value
("N/A", "-", a bare IP literal, etc.) -- is never duplicated here.
pipeline.company_enrichment.website_contact_provider._normalize_website_candidate()
(already built, already tested) is what WebsiteContactProvider.lookup()
runs internally on every candidate, exactly as it already does for the
in-process caller today -- this module's job is only to reject a
structurally malformed HTTP request before it ever reaches that logic,
not to re-decide what counts as a valid domain.

`extra="forbid"` on every model here is deliberate strict validation
(reject unknown fields outright) per the execution plan's own request/
response contract requirement, not just FastAPI's default behavior.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_COMPANY_NAME_LENGTH = 300
MAX_WEBSITE_LENGTH = 500
MAX_CORRELATION_ID_LENGTH = 100
MAX_FIELD_NAME_LENGTH = 50
MAX_VALUE_LENGTH = 500


class LookupRequest(BaseModel):
    """The worker's single request shape. `website` is expected to
    already be resolved by the caller (execution plan S4.8/S6.1 -- the
    main-API-side adapter reads Company.website/google_website itself and
    sends the result here) -- the worker never queries a database for it.

    `correlation_id` carries the caller's own `run_id`
    (company_enrichment_jobs.run_id) when called for real, purely for log
    correlation across both services (S4.7) -- it is never used for
    authorization, deduplication, or any control-flow decision here."""

    model_config = ConfigDict(extra="forbid")

    company_id: int = Field(..., gt=0)
    company_name: str = Field(..., min_length=1, max_length=MAX_COMPANY_NAME_LENGTH)
    website: str | None = Field(default=None, max_length=MAX_WEBSITE_LENGTH)
    correlation_id: str | None = Field(
        default=None, max_length=MAX_CORRELATION_ID_LENGTH
    )


class FactModel(BaseModel):
    """Wire shape of one pipeline.company_enrichment.provider.ProviderFact --
    field names and meaning are identical, this is purely a serialization
    boundary, not a re-definition."""

    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(..., max_length=MAX_FIELD_NAME_LENGTH)
    value: str = Field(..., max_length=MAX_VALUE_LENGTH)
    confidence: float | None = None
    source_url: str | None = None
    raw_value: str | None = None
    extraction_method: str | None = None


class LookupResponse(BaseModel):
    """Wire shape of one pipeline.company_enrichment.provider.ProviderResult,
    plus the echoed `correlation_id`. `matched=False` with `error=None`
    means a clean no-match (provider.py's own "no match is a valid
    outcome, never an error" contract, preserved unchanged across the HTTP
    boundary) -- `error` set covers every provider-level failure category
    (ssrf_blocked, robots_disallowed, dns_nxdomain, timeout, ...) as well
    as this worker's own worker_internal_error/timeout tags (worker/app.py)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    matched: bool
    facts: list[FactModel] = Field(default_factory=list)
    error: str | None = None
    correlation_id: str | None = None
