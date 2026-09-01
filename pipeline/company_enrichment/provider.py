"""Provider abstraction for on-demand company enrichment (RFC Phase 0:
docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md S4).

Unifies the shape of the two existing provider idioms this repo already
has -- pipeline.registry_verification.base.VerificationProvider (Protocol)
and pipeline.google_enrichment.provider.GoogleEnrichmentProvider (ABC) --
into one interface new enrichment providers implement. Deliberately
synchronous (plain SQLAlchemy Session, no async driver in use anywhere
else in this repo -- every existing route in api/internal.py and
api/main.py is a sync `def`, not `async def`), a deliberate deviation from
the RFC's illustrative `async def lookup(...)` snippet to match this
codebase's actual conventions rather than introduce a new async-only
edge.

A provider must NEVER invent a value: `ProviderResult.facts` only ever
contains facts the provider actually found. A missing field is simply
absent from `facts`, never a guessed or default value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class EnrichmentRequest:
    """What a provider needs to look up one company."""

    company_id: int
    company_name: str
    website: str | None = None


@dataclass(frozen=True)
class ProviderFact:
    """One fact a provider found, ready to be persisted to
    company_enrichment_fields (RFC S5) by the orchestrator's writer.

    `source_url`, `raw_value`, and `extraction_method` are Phase 3A additions
    (docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2.2, "Option A") carried
    on this in-memory dataclass ahead of the schema migration that would let
    write_enrichment_facts() actually persist them -- that migration is not
    applied anywhere (not even locally) as of Phase 3A, so these three fields
    are currently dropped, not written, by the existing writer. They exist
    here so WebsiteContactProvider's richer evidence is available in-process
    (e.g. for a caller inspecting ProviderResult directly, or for tests) and
    so the future writer update only has to start reading fields that
    already exist on this dataclass, not add them. Optional with a None
    default specifically so every existing ProviderFact construction site
    (OrgBookAdapter, tests) keeps working unchanged."""

    field_name: str
    value: str
    confidence: float | None = None
    source_url: str | None = None
    raw_value: str | None = None
    extraction_method: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    """A single provider's outcome for one lookup.

    `matched=False` with `error=None` means "the provider ran cleanly and
    found nothing" (golden case #9: no match still returns a valid
    result, never an error). `error` set means the provider call itself
    failed or timed out (golden case #6) -- the orchestrator records this
    in company_enrichment_jobs.providers_attempted and moves on to the
    next provider; it never raises out of the cascade.
    """

    provider: str
    matched: bool
    facts: tuple[ProviderFact, ...] = field(default_factory=tuple)
    error: str | None = None


class EnrichmentProvider(Protocol):
    """Each enrichment provider implements this interface."""

    name: str
    is_fact_source: bool  # True for orgbook/website/google, False for a
    # structuring-only step (e.g. the local-LLM step, RFC Phase 6 -- not
    # implemented by this phase)

    def lookup(self, session: Session, request: EnrichmentRequest) -> ProviderResult:
        """Return raw, provider-scoped facts. Must not invent a value --
        missing = absent from ProviderResult.facts, never a guess.

        MANDATORY for any implementation that performs network I/O
        (safety review requirement, RFC Phase 3+): the implementation
        MUST set its own request-level timeout on every outbound call
        (e.g. `requests.get(url, timeout=N)`, never an untimed call).
        pipeline.company_enrichment.orchestrator._call_provider_with_timeout()
        wraps every lookup() call in a thread-based wall-clock deadline,
        but Python cannot forcibly kill a thread -- that wrapper can only
        stop the CALLER from waiting past the deadline, never force this
        method's own blocked I/O call to actually return. A lookup() with
        no timeout of its own turns a bounded-latency provider outage
        into an unbounded leaked thread + held DB connection per call
        (see _call_provider_with_timeout()'s docstring for the exact
        accumulation this causes). OrgBookAdapter has no network I/O
        (local-DB-only) so this does not apply to it; it becomes a hard
        requirement starting with the website provider (RFC Phase 3)."""
