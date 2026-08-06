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
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.models import (
    ArchTender,
    CommercialTender,
    EarlySignalEvent,
    LinkedInSignal,
    NewsSignal,
    Permit,
    PipelineRun,
    RedditSignal,
    Tender,
)
from db.pipeline_coordinator_tables import pipeline_coordinator_runs

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


# Fixed, non-parameterized classification labels. classify_run_error()
# only ever returns one of these constants -- never any substring of the
# input -- so a raw error containing a connection string, bearer token, or
# API key can never leak into the response, regardless of which category
# it happens to match.
_ERROR_SUMMARY_TIMEOUT = "timeout"
_ERROR_SUMMARY_HTTP_4XX = "http_4xx"
_ERROR_SUMMARY_HTTP_5XX = "http_5xx"
_ERROR_SUMMARY_DATABASE = "database"
_ERROR_SUMMARY_VALIDATION = "validation"
_ERROR_SUMMARY_UNKNOWN = "unknown"

_TIMEOUT_MARKERS = ("timeout", "timed out")
_HTTP_5XX_RE = re.compile(r"\b5\d{2}\b")
_HTTP_4XX_RE = re.compile(r"\b4\d{2}\b")
_DATABASE_MARKERS = (
    "database",
    "postgres",
    "psycopg",
    "sqlalchemy",
    "connection refused",
    "relation does not exist",
    "integrityerror",
    "operationalerror",
    "could not connect",
)
_VALIDATION_MARKERS = (
    "validation",
    "valueerror",
    "keyerror",
    "typeerror",
    "required field",
    "invalid ",
)


def classify_run_error(raw: str | None) -> tuple[bool, str | None]:
    """Classify a raw pipeline_runs.error value into (error_present,
    error_summary) WITHOUT ever returning any part of the original text.

    error_summary is always one of: timeout, http_4xx, http_5xx, database,
    validation, unknown -- a fixed label, never interpolated from the
    input. Classification is keyword-matching on a lowercased copy of the
    input purely to pick a label; that lowercased copy itself is
    discarded and never part of the return value. Checked in this order
    (most specific/actionable first): timeout, http 5xx, http 4xx,
    database, validation, else unknown.
    """
    if not raw:
        return False, None

    lowered = str(raw).lower()

    if any(marker in lowered for marker in _TIMEOUT_MARKERS):
        return True, _ERROR_SUMMARY_TIMEOUT
    if _HTTP_5XX_RE.search(lowered):
        return True, _ERROR_SUMMARY_HTTP_5XX
    if _HTTP_4XX_RE.search(lowered):
        return True, _ERROR_SUMMARY_HTTP_4XX
    if any(marker in lowered for marker in _DATABASE_MARKERS):
        return True, _ERROR_SUMMARY_DATABASE
    if any(marker in lowered for marker in _VALIDATION_MARKERS):
        return True, _ERROR_SUMMARY_VALIDATION
    return True, _ERROR_SUMMARY_UNKNOWN


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
    return {
        "run_id": row["run_id"],
        "phase": row["phase"],
        "lease_valid": lease_valid,
        "lease_expires_at": _iso(row["lease_expires_at"]),
        "started_at": _iso(row["created_at"]),
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
    FreshnessSource("Federal", Tender, "scraped_at", "source", "buyandsell.gc.ca"),
    FreshnessSource("MERX Open", Tender, "scraped_at", "source", "merx.com"),
    FreshnessSource("MERX Architecture", ArchTender, "scraped_at"),
    FreshnessSource("Commercial", CommercialTender, "scraped_at"),
    FreshnessSource("Surrey Permits", Permit, "scraped_at", "source", "surrey"),
    FreshnessSource("Burnaby Permits", Permit, "scraped_at", "source", "burnaby"),
    FreshnessSource("Vancouver Permits", Permit, "scraped_at", "source", "vancouver"),
    FreshnessSource("Reddit Signals", RedditSignal, "scraped_at"),
    FreshnessSource("News Signals", NewsSignal, "scraped_at"),
    FreshnessSource("LinkedIn Signals", LinkedInSignal, "scraped_at"),
    FreshnessSource("Early Signal Events", EarlySignalEvent, "scraped_at"),
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
