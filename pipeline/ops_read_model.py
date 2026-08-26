"""Read-only data-shaping logic for the Mission Control M1 ops API
(api/ops.py). No FastAPI imports here on purpose -- every function takes
plain values or a SQLAlchemy Session and returns plain dict/dataclass
data, so the normalization rules can be unit-tested without spinning up
routes or a real database.

Hard rules this module exists to enforce (see docs/architecture/
MISSION_CONTROL_M1_READONLY_API.md for the full contract):
  - pipeline_runs.status='running' alone is never treated as proof of an
    active process (it's an unbounded, lease-less table -- see the R1/R2
    investigation). A row only normalizes to "active" when a currently
    valid pipeline_coordinator_runs lease backs the same run_id.
  - A pipeline_coordinator_runs row with status='active' but an EXPIRED
    lease is never reported as the active_run either -- an expired lease
    means nothing is really holding this run anymore, even though nobody
    has run the reclaim step yet. This is the exact "hung process looks
    alive" failure Mission Control must not repeat.
  - pipeline_runs.error is never returned verbatim. It can contain
    connection strings, bearer tokens, API keys, or other operational
    detail that has no business being in a dashboard response. Callers
    only ever see error_present (bool) + error_summary (one of a fixed,
    non-parameterized set of labels) -- never a substring of the original
    text.
  - Every DB-touching function here degrades to None/empty/"unknown"
    instead of raising when a table doesn't exist yet or the query fails
    -- callers (api/ops.py) must never let this module's failures become
    an unhandled 500.
  - Nothing here writes, migrates, or calls init_db().
  - (M2B) The container-local PID-file lock (get_container_lock_status())
    is a DIFFERENT source of truth from pipeline_coordinator_runs and must
    never be blended into active_run/expired_lease_run -- it only proves
    "this container's own lock file is held right now", says nothing
    about other Railway replicas, and a coordinator active_run can exist
    while this container's own lock is not held (the run may have started
    on a different container) or vice versa.
  - (M2B) The AI-scoring / company-intelligence / arch-company-intelligence
    steps of pipeline.run.run_pipeline() are not persisted anywhere (see
    AI_PIPELINE_TELEMETRY below) -- an honest capability=unavailable flag,
    never a fabricated health/configured status. Do not read
    Resend/Anthropic/n8n/Railway environment variables here even for a
    boolean "configured" check -- that was explicitly rejected as not
    proving anything useful about whether they actually work.
  - (M3E-A) The Surrey identity scheduler's equivalent capability flag is
    NOT here -- it moved to
    pipeline.ops_jobs_read_model.surrey_identity_scheduler_telemetry_capability()
    once M3C gave it a real writer, since "does run history exist" is now
    an actually-checkable question (ops_job_run schema + flag state)
    rather than a fixed constant. See that function's docstring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.models import (
    ArchTender,
    CommercialTender,
    Company,
    EarlySignalEvent,
    LinkedInSignal,
    NewsSignal,
    Permit,
    PipelineRun,
    RedditSignal,
    Tender,
)
from db.pipeline_coordinator_tables import pipeline_coordinator_runs
from pipeline.error_classification import classify_run_error
from pipeline.executor import pipeline_status

_TERMINAL_RUN_STATUSES = frozenset({"success", "failed", "skipped"})
_COORDINATOR_SCOPE = "tender_data"

# Freshness thresholds (M1 defaults -- documented, adjustable, not hidden).
# NOTE: these describe DATA freshness (when a row was last written), not
# scraper-run health -- see the module docstring and the docs page.
FRESHNESS_HEALTHY_HOURS = 24.0
FRESHNESS_DEGRADED_HOURS = 72.0

_COORDINATOR_SCHEMA_CHECK_SQL = text(
    "SELECT to_regclass('public.pipeline_coordinator_runs') IS NOT NULL "
    "AND to_regclass('public.pipeline_coordinator_steps') IS NOT NULL"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------
# pipeline_runs normalization (pure -- no DB access)
# ---------------------------------------------------------------------


def parse_counts_json(raw: str | None) -> dict[str, Any]:
    """Best-effort parse of PipelineRun.counts_json. Never raises: missing
    or malformed JSON (or JSON that isn't an object) becomes {}."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# classify_run_error() now lives in pipeline/error_classification.py
# (M3B) -- shared with pipeline/job_run.py so the security-critical
# "never return raw error text" logic has exactly one implementation.
# Imported above; kept referenced here so existing callers/tests that do
# `from pipeline import ops_read_model as rm; rm.classify_run_error(...)`
# keep working unchanged.


def normalize_pipeline_run_status(
    *,
    status: str,
    finished_at: datetime | None,
    run_id: str,
    coordinator_active_run_ids: frozenset[str],
) -> str:
    """Map a raw pipeline_runs.status value to the contract's
    normalized_status.

    - success / failed / skipped: passed through as-is (these are already
      unambiguous terminal outcomes recorded by the writer).
    - running, with run_id present in coordinator_active_run_ids (i.e.
      backed by a currently valid pipeline_coordinator_runs lease for the
      tender_data scope): "active". The coordinator lease takes priority
      over everything else -- it is the only source of truth this module
      trusts for "is something really running right now."
    - running, with no coordinator backing, finished_at is None: this is
      exactly the ambiguous case pipeline_runs cannot resolve on its own
      (no lease, no TTL, crashed workers never clean it up) --
      "stale_candidate", never "running".
    - anything else unrecognized: "unknown".
    """
    if status in _TERMINAL_RUN_STATUSES:
        return status
    if status == "running":
        if run_id in coordinator_active_run_ids:
            return "active"
        if finished_at is None:
            return "stale_candidate"
        return "unknown"
    return "unknown"


def build_run_payload(
    record: Any,
    *,
    coordinator_active_run_ids: frozenset[str],
) -> dict[str, Any]:
    """Shape one pipeline_runs row for the API response. `record` only
    needs the attributes PipelineRun has (id, run_id, step, status,
    started_at, finished_at, error, counts_json) -- accepting a plain
    object (not specifically the ORM class) keeps this unit-testable with
    a lightweight stand-in.

    error_present/error_summary replace the raw error text entirely --
    see classify_run_error()'s docstring for why."""
    normalized_status = normalize_pipeline_run_status(
        status=record.status,
        finished_at=record.finished_at,
        run_id=record.run_id,
        coordinator_active_run_ids=coordinator_active_run_ids,
    )
    error_present, error_summary = classify_run_error(record.error)
    return {
        "id": record.id,
        "run_id": record.run_id,
        "job_type": record.step,  # pipeline_runs has no separate job_type column; step is it
        "status": record.status,
        "normalized_status": normalized_status,
        "started_at": _iso(record.started_at),
        "finished_at": _iso(record.finished_at),
        "counts": parse_counts_json(record.counts_json),
        "error_present": error_present,
        "error_summary": error_summary,
    }


# ---------------------------------------------------------------------
# Coordinator-backed active-run lookup (DB access, degrades gracefully)
# ---------------------------------------------------------------------


def coordinator_schema_available(session: Session) -> bool:
    try:
        return bool(session.execute(_COORDINATOR_SCHEMA_CHECK_SQL).scalar_one())
    except Exception:
        return False


def get_coordinator_active_run_ids(session: Session) -> frozenset[str]:
    """All run_ids currently backed by a valid (non-expired) active lease
    in pipeline_coordinator_runs, across all scopes. Returns an empty
    frozenset -- never raises -- if the schema doesn't exist or the query
    fails for any reason."""
    if not coordinator_schema_available(session):
        return frozenset()
    try:
        rows = (
            session.execute(
                select(pipeline_coordinator_runs.c.run_id).where(
                    pipeline_coordinator_runs.c.status == "active",
                    pipeline_coordinator_runs.c.lease_expires_at > _utc_now(),
                )
            )
            .scalars()
            .all()
        )
        return frozenset(rows)
    except Exception:
        return frozenset()


def _lease_row_payload(row: Any, *, lease_valid: bool) -> dict[str, Any]:
    """Shape one pipeline_coordinator_runs row for active_run/
    expired_lease_run. (M2B) Also reports success/stale_reclaimed, plus
    error_present/error_summary derived through the same
    classify_run_error() used for pipeline_runs.error -- the raw
    pipeline_coordinator_runs.error text (a Text column, "" by default,
    which can carry the same kind of operational detail as
    pipeline_runs.error) is never returned verbatim here either."""
    error_present, error_summary = classify_run_error(row["error"])
    return {
        "run_id": row["run_id"],
        "phase": row["phase"],
        "lease_valid": lease_valid,
        "lease_expires_at": _iso(row["lease_expires_at"]),
        "started_at": _iso(row["created_at"]),
        "success": row["success"],
        "stale_reclaimed": row["stale_reclaimed"],
        "error_present": error_present,
        "error_summary": error_summary,
    }


def get_coordinator_summary(session: Session, *, backend: str) -> dict[str, Any]:
    """The `coordinator` block of GET /api/ops/summary. Never raises: a
    missing schema or query failure degrades to active_run=None,
    expired_lease_run=None, schema_available=False rather than a 500.

    active_run is populated ONLY when status='active' AND the lease has
    not expired -- a status='active' row with an expired lease is a hung
    process nobody has reclaimed yet, not a live one, and is reported
    separately as expired_lease_run so the UI can distinguish "nothing is
    running" from "something claims to be running but isn't really" from
    "we can't tell" (schema_available=False)."""
    schema_available = coordinator_schema_available(session)
    active_run: dict[str, Any] | None = None
    expired_lease_run: dict[str, Any] | None = None

    if schema_available:
        try:
            row = (
                session.execute(
                    select(pipeline_coordinator_runs).where(
                        pipeline_coordinator_runs.c.pipeline_scope
                        == _COORDINATOR_SCOPE,
                        pipeline_coordinator_runs.c.status == "active",
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                lease_expires = row["lease_expires_at"]
                lease_valid = lease_expires is not None and lease_expires > _utc_now()
                if lease_valid:
                    active_run = _lease_row_payload(row, lease_valid=True)
                else:
                    expired_lease_run = _lease_row_payload(row, lease_valid=False)
        except Exception:
            active_run = None
            expired_lease_run = None

    return {
        "backend": backend,
        "schema_available": schema_available,
        "active_run": active_run,
        "expired_lease_run": expired_lease_run,
    }


# ---------------------------------------------------------------------
# Container-local liveness (M2B) -- no DB access, no session needed
# ---------------------------------------------------------------------


def get_container_lock_status() -> dict[str, Any]:
    """The `system.container_lock` block of GET /api/ops/summary.

    Wraps pipeline.executor.pipeline_status(), itself backed by the
    file-based PID lock in pipeline/lock.py (.pipeline.lock, with a real
    os.kill(pid, 0) liveness check -- self-healing if the process that
    held it has died). This is a DIFFERENT, independent source of truth
    from pipeline_coordinator_runs.active_run: it only says "this
    container's own lock file is currently held", nothing about other
    Railway replicas, and must never be blended with or substituted for
    the coordinator lease. `scope` is always "current_container" so
    callers cannot mistake this for a fleet-wide signal.

    Three-state result -- "the lock file was read and nothing holds it"
    is NOT the same claim as "we couldn't tell", and this function must
    never collapse the two into the same running=False shape:
      - status="idle": read succeeded, no live PID holds the lock.
        running=False, pid=None.
      - status="active": read succeeded, a live PID holds the lock.
        running=True, pid=<int>.
      - status="unknown": pipeline_status() itself raised. running=None,
        pid=None -- an honest "couldn't tell", never reported as idle
        (which would silently hide a possibly-active run) or as active
        (which would fabricate one). Never raises, never returns the
        exception's text, and never logs it -- only the boolean/None
        result is observable here."""
    try:
        status = pipeline_status()
    except Exception:
        return {
            "status": "unknown",
            "running": None,
            "scope": "current_container",
            "pid": None,
        }

    running = bool(status.get("running"))
    pid = status.get("pid") or None
    return {
        "status": "active" if running else "idle",
        "running": running,
        "scope": "current_container",
        "pid": pid if running else None,
    }


# ---------------------------------------------------------------------
# Honest capability flags (M2B) -- static, not a health/configured check
#
# (M3E-A) The Surrey identity scheduler's flag used to live here too
# (SURREY_IDENTITY_SCHEDULER_TELEMETRY, fixed available=False) but was
# removed once M3C gave it a real writer -- see
# pipeline.ops_jobs_read_model.surrey_identity_scheduler_telemetry_capability()
# for its dynamic replacement, computed per-request from the
# ENABLE_SURREY_JOB_RUN_TELEMETRY flag and a real ops_job_run schema
# check, not a fixed constant.
# ---------------------------------------------------------------------

# Run history for pipeline.run.run_pipeline()'s AI-scoring /
# company-intelligence / arch-company-intelligence steps: no pipeline_runs
# row, no coordinator row, only print() to stdout. A handful of Company
# columns carry real timestamps for specific enrichment sub-steps (see
# FRESHNESS_SOURCES below), but there is no run-history/success signal for
# the AI pipeline steps themselves.
AI_PIPELINE_TELEMETRY: dict[str, Any] = {
    "available": False,
    "reason": "run_history_not_persisted",
}


# ---------------------------------------------------------------------
# Source freshness (DB access, degrades gracefully per source)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class FreshnessSource:
    name: str
    model: Any
    timestamp_column: str
    filter_column: str | None = None
    filter_value: str | None = None


FRESHNESS_SOURCES: tuple[FreshnessSource, ...] = (
    # (M3G/M3H) last_seen_at, not scraped_at, for every source backed by
    # db/tender_presence.py::upsert_with_presence() (Tender -- both
    # Federal and MERX Open share this table via db/import_csv.py's
    # import_tenders() -- CommercialTender, and ArchTender). That
    # function unconditionally skips scraped_at on ON CONFLICT DO
    # UPDATE (it is insert-time only), while last_seen_at is refreshed
    # on every successful upsert, insert or update. A source whose scrape
    # mostly re-confirms already-known rows on a given day (few or zero
    # brand-new URLs) will have its MAX(scraped_at) stop advancing even
    # though the scraper ran and every row was re-confirmed -- this was
    # first caught for MERX Architecture (a small, mostly-static
    # "Open & Ongoing" listing) and fixed there in PR #135; the same
    # code path and the same latent risk apply identically to Federal,
    # MERX Open, and Commercial, so they get the same fix here.
    # last_seen_at is the correct "scraper confirmed this row today"
    # signal; scraped_at's value, its insert-time semantics, and
    # db/tender_presence.py itself are all unchanged by this fix -- this
    # is a read-model-only change. See
    # tests/unit/test_merx_architecture_freshness.py and
    # tests/unit/test_federal_merx_open_commercial_freshness.py.
    #
    # pipeline/data_coverage_audit.py computes its own, separate
    # scraped_at-based freshness reading for these same tables and is
    # NOT updated by this change -- a deliberate, explicit follow-up,
    # not an oversight (see that module for its own MAX(scraped_at)
    # queries, untouched here).
    FreshnessSource("Federal", Tender, "last_seen_at", "source", "buyandsell.gc.ca"),
    FreshnessSource("MERX Open", Tender, "last_seen_at", "source", "merx.com"),
    FreshnessSource("MERX Architecture", ArchTender, "last_seen_at"),
    FreshnessSource("Commercial", CommercialTender, "last_seen_at"),
    FreshnessSource("Surrey Permits", Permit, "scraped_at", "source", "surrey"),
    FreshnessSource("Burnaby Permits", Permit, "scraped_at", "source", "burnaby"),
    FreshnessSource("Vancouver Permits", Permit, "scraped_at", "source", "vancouver"),
    FreshnessSource("Reddit Signals", RedditSignal, "scraped_at"),
    FreshnessSource("News Signals", NewsSignal, "scraped_at"),
    FreshnessSource("LinkedIn Signals", LinkedInSignal, "scraped_at"),
    FreshnessSource("Early Signal Events", EarlySignalEvent, "scraped_at"),
    # (M2B) These three measure enrichment DATA freshness only -- exactly
    # like every other row above, MAX(timestamp) proves a row was last
    # touched at that time and nothing about whether the run that touched
    # it succeeded or covered every company. There is no separate
    # run-history signal for any of these (see AI_PIPELINE_TELEMETRY).
    FreshnessSource("Google Enrichment", Company, "last_enriched_at"),
    FreshnessSource("CIP", Company, "cip_at"),
    FreshnessSource("Construction Tier", Company, "construction_tier_at"),
)


def _source_of_truth_label(source: FreshnessSource) -> str:
    table = source.model.__tablename__
    label = f"{table}.{source.timestamp_column}"
    if source.filter_column and source.filter_value:
        label += f" WHERE {source.filter_column}='{source.filter_value}'"
    return label


def _freshness_status(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours <= FRESHNESS_HEALTHY_HOURS:
        return "healthy"
    if hours <= FRESHNESS_DEGRADED_HOURS:
        return "degraded"
    return "stale"


def compute_source_freshness(
    session: Session, source: FreshnessSource
) -> dict[str, Any]:
    """One row of GET /api/ops/sources. A single bounded MAX() aggregate
    query per source -- no joins, no per-row scans, no COUNT(*) (row
    counts are intentionally out of scope for M1; see the docs).

    This measures DATA freshness (when the newest row was written), not
    scraper-run health -- a table can look "fresh" from a run that
    partially failed, and this function has no way to know that. See
    docs/architecture/MISSION_CONTROL_M1_READONLY_API.md.

    Never raises: any query failure (including a missing/renamed column
    or table -- not expected given these are existing mapped models, but
    guarded anyway since this endpoint must never 500 on a single bad
    source) degrades that one source to status=unknown."""
    column = getattr(source.model, source.timestamp_column)
    query = select(func.max(column))
    if source.filter_column and source.filter_value is not None:
        query = query.where(
            getattr(source.model, source.filter_column) == source.filter_value
        )

    try:
        latest_record = session.execute(query).scalar_one_or_none()
    except Exception:
        return {
            "name": source.name,
            "status": "unknown",
            "latest_record_at": None,
            "freshness_hours": None,
            "reason": "telemetry_not_available",
            "source_of_truth": _source_of_truth_label(source),
        }

    if latest_record is None:
        return {
            "name": source.name,
            "status": "unknown",
            "latest_record_at": None,
            "freshness_hours": None,
            "reason": "telemetry_not_available",
            "source_of_truth": _source_of_truth_label(source),
        }

    if latest_record.tzinfo is None:
        latest_record = latest_record.replace(tzinfo=timezone.utc)
    freshness_hours = round((_utc_now() - latest_record).total_seconds() / 3600.0, 2)

    return {
        "name": source.name,
        "status": _freshness_status(freshness_hours),
        "latest_record_at": _iso(latest_record),
        "freshness_hours": freshness_hours,
        "reason": None,
        "source_of_truth": _source_of_truth_label(source),
    }


def compute_all_source_freshness(session: Session) -> list[dict[str, Any]]:
    return [compute_source_freshness(session, source) for source in FRESHNESS_SOURCES]


# ---------------------------------------------------------------------
# pipeline_runs listing (DB access)
# ---------------------------------------------------------------------

_LIST_HARD_CAP = 500


def list_pipeline_runs(
    session: Session,
    *,
    limit: int,
    job_type: str | None,
    normalized_status_filter: str | None,
    coordinator_active_run_ids: frozenset[str],
) -> list[dict[str, Any]]:
    """List pipeline_runs rows, most recent first, normalized. Filtering
    by normalized_status can't be pushed into SQL (it depends on the
    coordinator active-run set, computed in Python), so this overfetches
    up to a fixed hard cap, normalizes, filters, then truncates to
    `limit`. Documented tradeoff: a normalized_status filter combined
    with a large `limit` can return fewer rows than requested if stale/
    superseded rows dominate the most recent _LIST_HARD_CAP rows -- this
    is a bounded-cost, best-effort M1 endpoint, not a paginated one."""
    query = select(PipelineRun).order_by(
        PipelineRun.started_at.desc(), PipelineRun.id.desc()
    )
    if job_type:
        query = query.where(PipelineRun.step == job_type)
    query = query.limit(_LIST_HARD_CAP)

    records = session.execute(query).scalars().all()
    payloads = [
        build_run_payload(r, coordinator_active_run_ids=coordinator_active_run_ids)
        for r in records
    ]
    if normalized_status_filter:
        payloads = [
            p for p in payloads if p["normalized_status"] == normalized_status_filter
        ]
    return payloads[:limit]


def get_run_detail(
    session: Session,
    run_id: str,
    *,
    coordinator_active_run_ids: frozenset[str],
) -> dict[str, Any] | None:
    """GET /api/ops/runs/{run_id}. Combines pipeline_runs step history
    with the pipeline_coordinator_runs row for the same run_id, if any.
    Returns None (caller maps to 404) only when NEITHER source has
    anything for this run_id.

    Unlike get_coordinator_summary()'s active_run, this reports the raw
    `status` column plus a computed `lease_valid` flag side by side
    (rather than a single field named "active") -- so it was already safe
    from the active-vs-expired-lease conflation this PR fixes elsewhere;
    no change needed here for that issue."""
    steps_query = (
        select(PipelineRun)
        .where(PipelineRun.run_id == run_id)
        .order_by(PipelineRun.started_at.asc(), PipelineRun.id.asc())
    )
    steps = [
        build_run_payload(r, coordinator_active_run_ids=coordinator_active_run_ids)
        for r in session.execute(steps_query).scalars().all()
    ]

    coordinator_row: dict[str, Any] | None = None
    if coordinator_schema_available(session):
        try:
            row = (
                session.execute(
                    select(pipeline_coordinator_runs).where(
                        pipeline_coordinator_runs.c.run_id == run_id
                    )
                )
                .mappings()
                .first()
            )
        except Exception:
            row = None
        if row is not None:
            lease_expires = row["lease_expires_at"]
            lease_valid = (
                row["status"] == "active"
                and lease_expires is not None
                and lease_expires > _utc_now()
            )
            coordinator_row = {
                "status": row["status"],
                "phase": row["phase"],
                "lease_valid": lease_valid,
                "lease_expires_at": _iso(lease_expires),
                "tender_scrape_finished_at": _iso(row["tender_scrape_finished_at"]),
                "import_started_at": _iso(row["import_started_at"]),
                "import_finished_at": _iso(row["import_finished_at"]),
                "finished_at": _iso(row["finished_at"]),
                "success": row["success"],
                "stale_reclaimed": row["stale_reclaimed"],
            }

    if not steps and coordinator_row is None:
        return None

    return {"run_id": run_id, "steps": steps, "coordinator": coordinator_row}
