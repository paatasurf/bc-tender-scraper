"""On-demand company enrichment job orchestrator (RFC Phase 2:
docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md S7).

Implements RFC S7 steps 2, 3, 5, 6 (partial), 7, 9 against
db/company_enrichment_tables.py (Core Table objects -- this module never
imports an ORM model for these two new tables, matching the "deliberately
NOT on Base.metadata" convention db/company_enrichment_tables.py's own
docstring documents).

Scope of THIS phase, deliberately narrow:
  - Providers: OrgBookAdapter only (pipeline.company_enrichment.
    orgbook_adapter) -- no website provider, no Google provider, no
    local-LLM structuring step. Callers may still pass a different
    `providers` tuple (used by this phase's own timeout-isolation test),
    but the default (_default_providers()) is OrgBook-only.
  - No budget/cost-cap check (RFC S7 step 4) -- OrgBook is free; a paid
    provider isn't wired yet, so there is nothing to budget against in
    this phase. ENRICHMENT_ENABLED itself is checked one layer up, by the
    API route (api/internal.py), not here -- this module is fully
    testable and callable independent of that flag.
  - Never touches companies.company_type or any other role-resolution
    field (RFC S2 point 2) -- this module writes ONLY to
    company_enrichment_fields / company_enrichment_jobs.
  - Never overwrites a verified=True field (RFC S12, golden case #8) --
    see write_enrichment_facts()'s verified-field guard below. (RFC S5's
    proper provenance-aware writer with superseded-not-deleted history for
    conflicting *external* sources is Phase 3 scope; this phase implements
    only the verified-field protection half of that contract, since
    OrgBook is the only provider wired.)
"""

from __future__ import annotations

import concurrent.futures
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.company_enrichment_tables import (
    company_enrichment_fields,
    company_enrichment_jobs,
)
from pipeline.company_enrichment.orgbook_adapter import OrgBookAdapter
from pipeline.company_enrichment.provider import (
    EnrichmentProvider,
    EnrichmentRequest,
    ProviderResult,
)

logger = logging.getLogger(__name__)

DEFAULT_STALE_DAYS = 30
DEFAULT_LEASE_TTL = timedelta(minutes=10)
DEFAULT_PROVIDER_TIMEOUT_S = (
    90  # RFC S10 ENRICHMENT_PROVIDER_TIMEOUT_S, revised per benchmark
)

# Known limitation (pre-PR review, not fixed in this phase): no heartbeat
# renews lease_expires_at while a cascade is actually running -- a job's
# lease is set once at start_or_join_job() and never touched again until
# _finish_job(). With DEFAULT_LEASE_TTL=10min and this phase's only wired
# provider (OrgBook, sub-second), the lease can never genuinely expire out
# from under a live cascade in practice. This stops being true once a
# provider whose real latency approaches the lease TTL is added (RFC
# Phases 3/6) -- add heartbeating (mirroring pipeline.job_run.heartbeat_job_run())
# to run_cascade_for_job() at that point, not before; run_cascade_for_job()
# already reports the run's REAL persisted status (not a hardcoded
# "success") if it finds itself reclaimed by the time it finishes, so a
# lease-vs-latency race degrades to an honest "failed" result today, never
# a silently wrong "success".

_VALID_TRIGGERS = frozenset({"profile_view", "agent", "manual"})
_TERMINAL_STATUSES = frozenset({"success", "failed", "partial_success"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_providers() -> tuple[EnrichmentProvider, ...]:
    return (OrgBookAdapter(),)


# ---------------------------------------------------------------------------
# RFC S7 step 2: cache check
# ---------------------------------------------------------------------------


def get_current_fields(session: Session, company_id: int) -> list[dict[str, Any]]:
    """Current (non-superseded) company_enrichment_fields rows for one
    company, most-recently-fetched first."""
    rows = (
        session.execute(
            select(company_enrichment_fields)
            .where(
                company_enrichment_fields.c.company_id == company_id,
                company_enrichment_fields.c.superseded_at.is_(None),
            )
            .order_by(company_enrichment_fields.c.fetched_at.desc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def is_cache_fresh(
    fields: list[dict[str, Any]], *, stale_days: int = DEFAULT_STALE_DAYS
) -> bool:
    """A company is "fresh" (golden case #3: cache hit, zero provider
    calls) when it has at least one current field and the most recently
    fetched one is within stale_days. No fields at all is never "fresh" --
    that is golden case #9 (no data yet), handled by actually running the
    provider cascade once."""
    if not fields:
        return False
    newest_fetched_at = fields[0]["fetched_at"]
    if newest_fetched_at.tzinfo is None:
        newest_fetched_at = newest_fetched_at.replace(tzinfo=timezone.utc)
    return (_utc_now() - newest_fetched_at) <= timedelta(days=stale_days)


def check_cache(
    session: Session, company_id: int, *, stale_days: int = DEFAULT_STALE_DAYS
) -> dict[str, Any] | None:
    """Returns an immediate response dict (no job created, zero provider
    calls) when the cache is fresh; None when the caller must proceed to
    the dedup/provider-cascade path."""
    fields = get_current_fields(session, company_id)
    if not is_cache_fresh(fields, stale_days=stale_days):
        return None
    return {
        "status": "cache_hit",
        "company_id": company_id,
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# RFC S7 step 3: in-flight dedup
# ---------------------------------------------------------------------------


def find_active_job(session: Session, company_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(company_enrichment_jobs).where(
                company_enrichment_jobs.c.company_id == company_id,
                company_enrichment_jobs.c.status == "running",
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def reclaim_stale_job(session: Session, run_id: str) -> bool:
    """Reactively reclaim ONE 'running' job whose lease has already
    expired -- the minimal restart-safety fix this phase's job lifecycle
    needs. Without this, a worker process killed mid-cascade (Railway's
    restartPolicyType="ON_FAILURE"/restartPolicyMaxRetries=10 in
    railway.toml is a real, recurring event here, not a hypothetical --
    the exact same justification migration 033's own SQL comment already
    gives for ops_job_runs' lease design) leaves its
    company_enrichment_jobs row status='running' forever: nothing else in
    this module ever revisits it, and
    ux_company_enrichment_jobs_company_active (the partial unique index
    backing dedup) then permanently blocks every future enrichment
    request for that company, silently joining the same dead run_id
    forever rather than surfacing an error or ever making progress.

    Race-safe by the same idempotent-UPDATE-rowcount pattern
    pipeline.job_run.finish_job_run() already uses: only mutates a row
    that is STILL 'running' AND whose lease is STILL expired at the
    moment of the UPDATE (re-checked, not just re-using the caller's
    earlier read) -- two concurrent reclaimers on the same run_id
    serialize on the UPDATE, and only one's rowcount is 1. Never reclaims
    a live run: the sole criterion is lease_expires_at, mirroring
    pipeline/run_coordinator_postgres.py::reap_stale_run()'s exact
    reclaim criterion for the tender_data coordinator (migration 032).

    Marks the reclaimed row 'failed' (not 'success' or 'partial_success')
    -- a job that never got to report its own outcome is honestly a
    failure, not a silent success; company_enrichment_fields is
    untouched either way (this only marks the JOB row, never invents or
    discards enrichment data).
    """
    result = session.execute(
        update(company_enrichment_jobs)
        .where(
            company_enrichment_jobs.c.run_id == run_id,
            company_enrichment_jobs.c.status == "running",
            company_enrichment_jobs.c.lease_expires_at <= _utc_now(),
        )
        .values(status="failed", finished_at=_utc_now())
    )
    session.commit()
    return result.rowcount == 1


def start_or_join_job(
    session: Session,
    company_id: int,
    *,
    trigger: str,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
) -> tuple[str, bool]:
    """Insert a new 'running' company_enrichment_jobs row for company_id,
    or -- if one is already in flight -- return ITS run_id instead
    (golden case #5: a concurrent second request for the same company
    must never start a second job). Relies on
    ux_company_enrichment_jobs_company_active (partial unique index on
    company_id WHERE status='running') via INSERT ... ON CONFLICT DO
    NOTHING, mirroring the same race-free pattern
    pipeline/run_coordinator_postgres.py already uses for its own
    lease-backed dedup.

    Returns (run_id, joined_existing).
    """
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(
            f"trigger must be one of {sorted(_VALID_TRIGGERS)}, got {trigger!r}"
        )

    run_id = str(uuid.uuid4())
    now = _utc_now()
    stmt = (
        pg_insert(company_enrichment_jobs)
        .values(
            run_id=run_id,
            company_id=company_id,
            trigger=trigger,
            status="running",
            providers_attempted=[],
            started_at=now,
            finished_at=None,
            lease_expires_at=now + lease_ttl,
        )
        .on_conflict_do_nothing(
            index_elements=["company_id"],
            index_where=company_enrichment_jobs.c.status == "running",
        )
        .returning(company_enrichment_jobs.c.run_id)
    )
    inserted_run_id = session.execute(stmt).scalar_one_or_none()
    session.commit()

    if inserted_run_id is not None:
        return inserted_run_id, False

    existing = find_active_job(session, company_id)
    if existing is None:
        # Extremely narrow race: the in-flight job finished between our
        # failed INSERT and this SELECT. Safe to just start fresh.
        return start_or_join_job(
            session, company_id, trigger=trigger, lease_ttl=lease_ttl
        )

    if existing["lease_expires_at"] <= _utc_now():
        # The blocking job is not live -- its worker died (crash, OOM,
        # Railway restart) without ever reaching _finish_job(). Reclaim it
        # instead of joining a run_id that will never make progress or
        # report an outcome (see reclaim_stale_job()'s docstring for the
        # exact deadlock this prevents), then retry as a fresh start.
        reclaim_stale_job(session, existing["run_id"])
        return start_or_join_job(
            session, company_id, trigger=trigger, lease_ttl=lease_ttl
        )

    return existing["run_id"], True


# ---------------------------------------------------------------------------
# RFC S7 step 7 (partial): provenance-aware writer, verified-field guard only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteOutcome:
    written: tuple[str, ...]
    skipped_verified: tuple[str, ...]


def write_enrichment_facts(
    session: Session,
    company_id: int,
    facts: list[Any],
    *,
    source: str,
    run_id: str,
) -> WriteOutcome:
    """Persist provider facts to company_enrichment_fields.

    Verified-field protection (RFC S12, golden case #8): if ANY current
    row for (company_id, field_name) -- regardless of source -- has
    verified=True, that field is skipped entirely, never superseded by an
    automated result. Otherwise, any existing current row for THIS EXACT
    (company_id, field_name, source) triple is marked superseded (never
    deleted -- RFC S5 provenance), and the new value is inserted as the
    current row.
    """
    now = _utc_now()
    written: list[str] = []
    skipped: list[str] = []

    for fact in facts:
        verified_row = session.execute(
            select(company_enrichment_fields.c.id).where(
                company_enrichment_fields.c.company_id == company_id,
                company_enrichment_fields.c.field_name == fact.field_name,
                company_enrichment_fields.c.superseded_at.is_(None),
                company_enrichment_fields.c.verified.is_(True),
            )
        ).first()
        if verified_row is not None:
            skipped.append(fact.field_name)
            continue

        session.execute(
            update(company_enrichment_fields)
            .where(
                company_enrichment_fields.c.company_id == company_id,
                company_enrichment_fields.c.field_name == fact.field_name,
                company_enrichment_fields.c.source == source,
                company_enrichment_fields.c.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )
        session.execute(
            company_enrichment_fields.insert().values(
                company_id=company_id,
                field_name=fact.field_name,
                value=fact.value,
                source=source,
                confidence=fact.confidence,
                verified=False,
                fetched_at=now,
                superseded_at=None,
                run_id=run_id,
            )
        )
        written.append(fact.field_name)

    session.commit()
    return WriteOutcome(written=tuple(written), skipped_verified=tuple(skipped))


# ---------------------------------------------------------------------------
# RFC S7 steps 5, 6, 9: provider cascade + terminal status
# ---------------------------------------------------------------------------


def _finish_job(session: Session, run_id: str, *, status: str) -> bool:
    """Idempotent terminal transition, mirroring pipeline.job_run.finish_job_run()'s
    exact precedent: only mutates a row currently 'running' (never checks
    lease expiry -- the worker reporting in is still authoritative for its
    own outcome, even moments after its lease technically lapsed).
    A no-op (returns False) when the run is already terminal -- e.g.
    reclaim_stale_job() already flipped it to 'failed' while this worker
    was still (genuinely or not) working. Returns True iff THIS call
    performed the transition, so the caller can report the run's REAL
    persisted status rather than assume its own requested status won."""
    if status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_TERMINAL_STATUSES)}, got {status!r}"
        )
    result = session.execute(
        update(company_enrichment_jobs)
        .where(
            company_enrichment_jobs.c.run_id == run_id,
            company_enrichment_jobs.c.status == "running",
        )
        .values(status=status, finished_at=_utc_now())
    )
    session.commit()
    return result.rowcount == 1


def _resolve_cascade_status(providers_attempted: list[str]) -> str:
    """Semantic status for a completed cascade -- mirrors
    pipeline/runs.py::_resolve_status()'s exact committed_chunks/
    write_failures distinction (success / partial_success / failed),
    generalized from "chunks" to "providers":

      - no provider errored or timed out -> "success", REGARDLESS of
        whether any provider actually matched anything. A clean run that
        legitimately found nothing is still a successful run of the
        cascade (golden case #9: "no match... never an error") -- the
        DATA gap this leaves is a read-model concern (RFC S8.1's
        enrichment_status: "no_data"), not a job-mechanics failure. This
        is the fix for the semantic bug this review found: every
        provider outcome (ok, error, AND timeout) used to collapse into
        "success" unconditionally -- a provider timing out was
        previously indistinguishable, at the job-status level, from
        every provider succeeding cleanly.
      - at least one provider errored/timed out AND at least one other
        provider ran cleanly ("ok") -> "partial_success": real progress
        was made (or at least attempted cleanly) despite a partial
        failure, matching _resolve_status()'s "write_failures with
        committed_chunks > 0 -> partial_success" exactly. NOTE: an "ok"
        provider that ran cleanly but found no match still counts as
        "ok" here (it didn't fail) -- partial_success specifically means
        "at least one provider broke," not "at least one provider found
        nothing."
      - at least one provider errored/timed out and NONE ran cleanly ->
        "failed": every attempted provider broke; this is categorically
        different from a clean no-match (nothing was actually verified
        to be absent -- the cascade itself couldn't complete its job).
      - no providers attempted at all -> "success" (vacuous case; no
        default provider list is ever empty in practice, but this keeps
        the function total rather than raising on an edge case).

    No retry logic here or anywhere this function is called -- this is
    purely a terminal-status classification, never a trigger to re-run
    anything.
    """
    ok_count = sum(1 for entry in providers_attempted if entry.endswith(":ok"))
    bad_count = sum(
        1
        for entry in providers_attempted
        if entry.endswith(":error") or entry.endswith(":timeout")
    )
    if bad_count == 0:
        return "success"
    if ok_count > 0:
        return "partial_success"
    return "failed"


def _call_provider_with_timeout(
    provider: EnrichmentProvider, request: EnrichmentRequest, timeout_s: float
) -> tuple[ProviderResult | None, str]:
    """Run provider.lookup() with a REAL, enforced wall-clock timeout.

    Bugbot finding fix: the previous implementation called
    provider.lookup() synchronously and measured elapsed time only AFTER
    it had already fully returned -- that can never interrupt a
    genuinely hung call (e.g. a network read blocking indefinitely past
    timeout_s), which would have blocked this entire cascade, and the
    whole background task, forever. This runs the call in its own
    worker thread and enforces the timeout via
    concurrent.futures.Future.result(timeout=...), which raises and
    returns control to the caller the moment the deadline passes,
    regardless of whether the underlying call has finished.

    Python cannot forcibly kill a thread, so a timed-out thread is
    abandoned (never joined/waited on) rather than blocked on again --
    it uses its OWN independent DB session (get_session(), never the
    caller's `session`), specifically so an abandoned thread that is
    still running in the background can never race against or corrupt
    the orchestrator's own session, which SQLAlchemy's Session is not
    designed to tolerate from multiple threads concurrently. Whatever
    that orphaned thread eventually returns (or raises) is simply
    discarded when it finishes.

    Returns (result_or_None, tag) where tag is exactly one of
    "ok" | "error" | "timeout".
    """

    def _run() -> ProviderResult:
        from db.connection import get_session

        thread_session = get_session()
        try:
            return provider.lookup(thread_session, request)
        finally:
            thread_session.close()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_run)
    try:
        result = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False)
        return None, "timeout"
    except Exception:  # noqa: BLE001 -- provider errors are isolated per RFC S7 step 5
        executor.shutdown(wait=False)
        return None, "error"
    executor.shutdown(wait=False)
    if result.error is not None:
        return result, "error"
    return result, "ok"


def _mark_job_failed_best_effort(run_id: str) -> None:
    """Last-resort safety net (Bugbot finding fix): guarantees a job
    reaches a terminal status even when the writer or the finish
    transition itself raises an unexpected exception -- e.g. a broken
    connection, a constraint violation, or anything else that would
    otherwise leave the row 'running' until its lease naturally expires
    10 minutes later and needs a FUTURE, unrelated request to reclaim it
    (reclaim_stale_job()) rather than being marked failed immediately by
    the job that actually broke.

    Uses a brand-new, independent session -- the caller's own `session`
    may be unusable at this point (a raised DB exception typically
    poisons the transaction until rolled back, and this function must
    not assume the caller has done that). Idempotent, matching
    _finish_job()'s own contract (a no-op if the run is already
    terminal). Fail-open and swallows its OWN failures: this is a safety
    net, not a source of truth, and must never mask or replace the
    original exception the caller is about to re-raise."""
    try:
        from db.connection import get_session

        fresh_session = get_session()
        try:
            _finish_job(fresh_session, run_id, status="failed")
        finally:
            fresh_session.close()
    except Exception:  # noqa: BLE001 -- this IS the last-resort handler
        logger.exception(
            "[company_enrichment] failed to mark run_id=%s as failed after an unhandled exception",
            run_id,
        )


def run_cascade_for_job(
    session: Session,
    run_id: str,
    company_id: int,
    company_name: str,
    *,
    providers: tuple[EnrichmentProvider, ...] | None = None,
    timeout_s: float = DEFAULT_PROVIDER_TIMEOUT_S,
) -> dict[str, Any]:
    """RFC S7 steps 5, 6, 7, 9 for an ALREADY-STARTED job (run_id already
    exists, status='running', per start_or_join_job()). This is the part
    of the lifecycle with real provider latency -- the piece the API route
    (api/internal.py) schedules as a background task, per this RFC's own
    "no request handler thread may ever call a provider inline" principle
    (S7 preamble). Never raises for a provider failure (golden case #6) or
    a total no-match (golden case #9) -- both are valid, non-error
    outcomes.

    The whole body below is wrapped so that ANY unexpected exception --
    from the provider cascade, the writer, or the finish transition --
    still leaves the job in a genuine terminal status (never stuck
    'running') before propagating: see _mark_job_failed_best_effort().
    This is a guaranteed terminal transition, not a retry -- the
    exception itself is always re-raised, never swallowed or retried.
    """
    try:
        active_providers = providers if providers is not None else _default_providers()
        request = EnrichmentRequest(company_id=company_id, company_name=company_name)

        providers_attempted: list[str] = []
        facts_by_provider: dict[str, list[Any]] = {}
        any_matched = False

        for provider in active_providers:
            result, tag = _call_provider_with_timeout(provider, request, timeout_s)
            providers_attempted.append(f"{provider.name}:{tag}")
            if tag != "ok":
                continue
            if result.matched and result.facts:
                any_matched = True
                facts_by_provider.setdefault(provider.name, []).extend(result.facts)
            elif result.matched:
                any_matched = True

        written: list[str] = []
        skipped_verified: list[str] = []
        for provider_name, facts in facts_by_provider.items():
            outcome = write_enrichment_facts(
                session, company_id, facts, source=provider_name, run_id=run_id
            )
            written.extend(outcome.written)
            skipped_verified.extend(outcome.skipped_verified)
        write_outcome = WriteOutcome(
            written=tuple(written), skipped_verified=tuple(skipped_verified)
        )

        session.execute(
            update(company_enrichment_jobs)
            .where(company_enrichment_jobs.c.run_id == run_id)
            .values(providers_attempted=providers_attempted)
        )
        session.commit()

        requested_status = _resolve_cascade_status(providers_attempted)
        finished = _finish_job(session, run_id, status=requested_status)
        if finished:
            actual_status = requested_status
        else:
            # This run_id was no longer 'running' by the time we finished --
            # almost certainly reclaim_stale_job() already flipped it to
            # 'failed' while this cascade was still in flight (a lease-vs-
            # real-latency race; see reclaim_stale_job()'s docstring). Report
            # what the row ACTUALLY persisted, not the status this cascade
            # merely wished for -- a caller must never be told "success" for
            # a job the row itself disagrees with.
            actual_status = session.execute(
                select(company_enrichment_jobs.c.status).where(
                    company_enrichment_jobs.c.run_id == run_id
                )
            ).scalar_one()

        return {
            "status": actual_status,
            "company_id": company_id,
            "run_id": run_id,
            "matched": any_matched,
            "fields_written": write_outcome.written,
            "fields_skipped_verified": write_outcome.skipped_verified,
            "providers_attempted": providers_attempted,
        }
    except Exception:
        _mark_job_failed_best_effort(run_id)
        raise
