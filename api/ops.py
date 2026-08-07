"""Mission Control M1 read-only ops API.

GET-only. Every route here is read-only by construction: no route takes a
body, no route calls a write/DDL function, and pipeline/ops_read_model.py
(the module that actually queries the database) never calls init_db(),
never issues INSERT/UPDATE/DELETE/DDL, and never touches Railway/n8n/
Clerk/Vercel/Resend/Anthropic.

Auth: gated behind the same X-Internal-Key mechanism already used for the
existing /internal/* router (api/internal.py::_require_internal_key,
api/main.py::verify_internal_key) -- reusing the established internal-only
guard rather than inventing a new one. See
docs/architecture/MISSION_CONTROL_M1_READONLY_API.md for the full
authorization analysis (what Clerk currently does, why it doesn't fit this
use case as-is, and what a real dashboard-facing auth story would need).

Every route that touches the database goes through _call_with_session(),
which is the single boundary between a transient DB hiccup (connection
pool exhaustion, a dropped connection between check_db_connection()
succeeding and get_session() being called, a query timeout, ...) and an
unhandled 500. check_db_connection() alone is not enough: it can succeed
and then get_session() or the query itself can still fail moments later.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request

from config.env import get_env
from db.connection import check_db_connection, get_session
from pipeline.ops_jobs_read_model import (
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    VALID_JOB_STATUS_FILTERS,
    get_job_type_summary,
    get_ops_job_run_detail,
    list_ops_job_runs,
    ops_job_run_schema_available,
    surrey_identity_scheduler_telemetry_capability,
)
from pipeline.ops_read_model import (
    AI_PIPELINE_TELEMETRY,
    FRESHNESS_SOURCES,
    compute_all_source_freshness,
    get_container_lock_status,
    get_coordinator_active_run_ids,
    get_coordinator_summary,
    get_run_detail,
    list_pipeline_runs,
)
from pipeline.scheduler import (
    SURREY_JOB_RUN_JOB_TYPE,
    scheduler_status,
    surrey_job_run_telemetry_enabled,
)

_VALID_COORDINATOR_BACKENDS = ("legacy", "postgres")

T = TypeVar("T")


def _require_internal_key(request: Request) -> None:
    """Same check as api/internal.py::_require_internal_key and
    api/main.py::verify_internal_key -- duplicated rather than imported to
    avoid a circular import between api.main and api.ops (api.main mounts
    this router), matching the pre-existing duplication between
    api/main.py and api/internal.py."""
    expected = os.getenv("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    key = request.headers.get("X-Internal-Key")
    if key is None or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


ops_router = APIRouter(
    prefix="/api/ops",
    tags=["ops"],
    dependencies=[Depends(_require_internal_key)],
)


def _resolve_coordinator_backend() -> str:
    """Read-only view of PIPELINE_COORDINATOR_BACKEND. Never raises --
    unlike pipeline.run_coordinator's dispatcher (which fails closed on an
    unrecognized value because it's about to route a real DB call), this
    is purely informational, so an unrecognized value reports "unknown"
    instead of erroring the whole summary response."""
    raw = get_env("PIPELINE_COORDINATOR_BACKEND", "legacy").strip().lower()
    return raw if raw in _VALID_COORDINATOR_BACKENDS else "unknown"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_with_session(fn: Callable[[Any], T]) -> tuple[bool, T | None]:
    """Acquire a session, call fn(session), always close it if (and only
    if) it was actually acquired. Returns (ok, result); any exception
    during acquisition OR execution is caught here and reported as
    (False, None) -- this is the single safety boundary described in the
    module docstring. Never lets a transient DB failure become an
    unhandled 500."""
    session = None
    try:
        session = get_session()
        return True, fn(session)
    except Exception:
        return False, None
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


_EMPTY_COORDINATOR = {
    "backend": None,
    "schema_available": False,
    "active_run": None,
    "expired_lease_run": None,
}


_EMPTY_JOB_TYPE_SUMMARY = {"last_run_at": None, "last_status": None, "counts": None}


def _load_ops_job_run_read_model(session: Any) -> dict[str, Any]:
    """Backs BOTH the `job_types` block and the dynamic Surrey telemetry
    capability below (M3E-A) with a single ops_job_run_schema_available()
    check per request, rather than doing it twice. Never fabricates a
    "healthy" reading: no schema, no row for this job_type yet, or a
    query failure all report the same all-null job_type summary -- see
    get_job_type_summary()'s docstring -- and schema_available=False,
    which surrey_identity_scheduler_telemetry_capability() below treats
    the same as "couldn't confirm the schema exists"."""
    schema_available = ops_job_run_schema_available(session)
    if schema_available:
        job_type_summary = get_job_type_summary(session, SURREY_JOB_RUN_JOB_TYPE)
    else:
        job_type_summary = dict(_EMPTY_JOB_TYPE_SUMMARY)
    return {"schema_available": schema_available, "job_type_summary": job_type_summary}


@ops_router.get("/summary")
def ops_summary() -> dict[str, Any]:
    db_ok = check_db_connection()
    scheduler = scheduler_status()
    backend = _resolve_coordinator_backend()

    coordinator = {**_EMPTY_COORDINATOR, "backend": backend}
    job_types: dict[str, Any] = {SURREY_JOB_RUN_JOB_TYPE: dict(_EMPTY_JOB_TYPE_SUMMARY)}
    # Conservative default: until proven otherwise (a real schema check
    # against a real session below), treat the ops_job_run schema as
    # unavailable -- this is what makes the Surrey capability below
    # degrade to schema_unavailable, never a false available=True, when
    # the database itself is down.
    ops_job_run_schema_ready = False
    if db_ok:
        ok, result = _call_with_session(
            lambda session: get_coordinator_summary(session, backend=backend)
        )
        if ok:
            coordinator = result

        ok, result = _call_with_session(_load_ops_job_run_read_model)
        if ok:
            ops_job_run_schema_ready = result["schema_available"]
            job_types = {SURREY_JOB_RUN_JOB_TYPE: result["job_type_summary"]}

    # (M3E-A) Dynamic, honest replacement for the old M2B static
    # available=False constant -- Surrey has been instrumented since M3C.
    # surrey_job_run_telemetry_enabled() is the one existing, non-secret,
    # already-audited helper for this flag; no other environment variable
    # is read here.
    surrey_telemetry_capability = surrey_identity_scheduler_telemetry_capability(
        telemetry_enabled=surrey_job_run_telemetry_enabled(),
        schema_available=ops_job_run_schema_ready,
    )

    return {
        "generated_at": _utc_now_iso(),
        "system": {
            "api_status": "healthy" if db_ok else "degraded",
            "database_connected": db_ok,
            "scheduler": scheduler,
            "coordinator": {
                "backend": coordinator["backend"],
                "schema_available": coordinator["schema_available"],
                "active_run": coordinator["active_run"],
                "expired_lease_run": coordinator["expired_lease_run"],
            },
            # (M2B) Independent of the coordinator lease above -- see
            # get_container_lock_status()'s docstring. Computed
            # unconditionally (no DB session needed) so a degraded
            # database never hides this signal.
            "container_lock": get_container_lock_status(),
        },
        # Not connected in M1 by design (task rule 8) -- an honest
        # not_connected, never a fabricated healthy status.
        "integrations": [
            {"name": "Railway", "status": "not_connected"},
            {"name": "n8n", "status": "not_connected"},
            {"name": "Clerk", "status": "not_connected"},
            {"name": "Vercel", "status": "not_connected"},
            {"name": "Resend", "status": "not_connected"},
            {"name": "AI Assistant", "status": "not_connected"},
        ],
        "capabilities": {
            "incidents_persisted": False,
            "scraper_heartbeats": False,
            "ai_chat_telemetry": False,
            # (M3E-A) Dynamic: reflects the real ENABLE_SURREY_JOB_RUN_TELEMETRY
            # flag + a real ops_job_run schema check -- see
            # surrey_identity_scheduler_telemetry_capability()'s docstring.
            "surrey_identity_scheduler_telemetry": surrey_telemetry_capability,
            # (M2B) AI-scoring / company-intelligence run history still has
            # no writer at all -- a fixed, honest "this run history
            # physically does not exist" flag, not a health check. See
            # pipeline/ops_read_model.py's module docstring.
            "ai_pipeline_telemetry": AI_PIPELINE_TELEMETRY,
        },
        # (M3E-A) Generic by construction, keyed by job_type -- only
        # SURREY_JOB_RUN_JOB_TYPE is populated today because it is the
        # only real ops_job_runs writer wired up so far (M3C). All-null
        # (never "healthy") when no row for this job_type exists yet, the
        # schema isn't applied on this environment, or the DB is down --
        # see _load_ops_job_run_read_model()/get_job_type_summary().
        "job_types": job_types,
    }


@ops_router.get("/runs")
def ops_runs(
    limit: int = 50,
    status: str | None = None,
    job_type: str | None = None,
) -> dict[str, Any]:
    bounded_limit = max(1, min(limit, 200))

    if not check_db_connection():
        return {
            "generated_at": _utc_now_iso(),
            "runs": [],
            "count": 0,
            "database_connected": False,
        }

    def _load(session):
        active_ids = get_coordinator_active_run_ids(session)
        return list_pipeline_runs(
            session,
            limit=bounded_limit,
            job_type=job_type,
            normalized_status_filter=status,
            coordinator_active_run_ids=active_ids,
        )

    ok, runs = _call_with_session(_load)
    if not ok:
        return {
            "generated_at": _utc_now_iso(),
            "runs": [],
            "count": 0,
            "database_connected": False,
        }

    return {
        "generated_at": _utc_now_iso(),
        "runs": runs,
        "count": len(runs),
        "database_connected": True,
    }


@ops_router.get("/runs/{run_id}")
def ops_run_detail(run_id: str) -> dict[str, Any]:
    if not check_db_connection():
        raise HTTPException(status_code=503, detail="Database unavailable")

    def _load(session):
        active_ids = get_coordinator_active_run_ids(session)
        return get_run_detail(session, run_id, coordinator_active_run_ids=active_ids)

    ok, detail = _call_with_session(_load)
    if not ok:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"No run found for run_id={run_id!r}"
        )

    return {"generated_at": _utc_now_iso(), **detail}


# ---------------------------------------------------------------------
# M3E-A -- ops_job_runs / ops_job_run_events (migration 033, M3B schema)
#
# Contract for schema/DB unavailability (documented, deliberate, mirrors
# the existing /api/ops/runs vs /api/ops/runs/{run_id} split above rather
# than inventing a new rule):
#   - GET /api/ops/jobs (a list endpoint, like /api/ops/runs): degrades to
#     an honest 200 with jobs=[], count=0, and schema_available/
#     database_connected flags describing why -- never a 503, never a 500.
#     A list is allowed to legitimately be empty; a caller polling this on
#     a fresh environment where migration 033 hasn't been applied yet
#     should see "no data yet", not an error.
#   - GET /api/ops/jobs/{run_id} (a detail endpoint, like
#     /api/ops/runs/{run_id}): a single resource lookup can only honestly
#     return 404 once it has actually been able to check for run_id and
#     found nothing. If the schema is missing or the DB call fails, this
#     function was never able to check, so it returns 503 (Database
#     unavailable) instead of a 404 that would falsely claim "checked, not
#     found". This is the same 503-vs-404 split /api/ops/runs/{run_id}
#     already uses for pipeline_runs.
# ---------------------------------------------------------------------

_EMPTY_JOBS_LIST_RESPONSE = {"jobs": [], "count": 0}


@ops_router.get("/jobs")
def ops_jobs(
    job_type: str | None = None,
    status: str | None = None,
    limit: int = LIST_DEFAULT_LIMIT,
) -> dict[str, Any]:
    if status is not None and status not in VALID_JOB_STATUS_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"status must be one of {sorted(VALID_JOB_STATUS_FILTERS)}, "
                f"got {status!r}"
            ),
        )
    bounded_limit = max(1, min(limit, LIST_MAX_LIMIT))

    if not check_db_connection():
        return {
            "generated_at": _utc_now_iso(),
            **_EMPTY_JOBS_LIST_RESPONSE,
            "database_connected": False,
            "schema_available": False,
        }

    def _load(session):
        if not ops_job_run_schema_available(session):
            return None
        return list_ops_job_runs(
            session, job_type=job_type, status=status, limit=bounded_limit
        )

    ok, jobs = _call_with_session(_load)
    if not ok:
        return {
            "generated_at": _utc_now_iso(),
            **_EMPTY_JOBS_LIST_RESPONSE,
            "database_connected": False,
            "schema_available": False,
        }
    if jobs is None:
        return {
            "generated_at": _utc_now_iso(),
            **_EMPTY_JOBS_LIST_RESPONSE,
            "database_connected": True,
            "schema_available": False,
        }

    return {
        "generated_at": _utc_now_iso(),
        "jobs": jobs,
        "count": len(jobs),
        "database_connected": True,
        "schema_available": True,
    }


@ops_router.get("/jobs/{run_id}")
def ops_job_detail(run_id: str) -> dict[str, Any]:
    if not check_db_connection():
        raise HTTPException(status_code=503, detail="Database unavailable")

    def _load(session):
        # False (never a valid detail payload -- only None/dict are) means
        # "schema not applied here", distinct from None ("checked, no such
        # run_id"). Both still map to the same 503 below -- see the
        # module-level contract comment above this endpoint.
        if not ops_job_run_schema_available(session):
            return False
        return get_ops_job_run_detail(session, run_id)

    ok, detail = _call_with_session(_load)
    if not ok or detail is False:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"No job run found for run_id={run_id!r}"
        )

    return {"generated_at": _utc_now_iso(), **detail}


def _unavailable_sources() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "status": "unknown",
            "latest_record_at": None,
            "freshness_hours": None,
            "reason": "telemetry_not_available",
            "source_of_truth": f"{s.model.__tablename__}.{s.timestamp_column}",
        }
        for s in FRESHNESS_SOURCES
    ]


@ops_router.get("/sources")
def ops_sources() -> dict[str, Any]:
    if not check_db_connection():
        return {
            "generated_at": _utc_now_iso(),
            "sources": _unavailable_sources(),
            "database_connected": False,
        }

    ok, sources = _call_with_session(compute_all_source_freshness)
    if not ok:
        return {
            "generated_at": _utc_now_iso(),
            "sources": _unavailable_sources(),
            "database_connected": False,
        }

    return {
        "generated_at": _utc_now_iso(),
        "sources": sources,
        "database_connected": True,
    }


@ops_router.get("/incidents")
def ops_incidents() -> dict[str, Any]:
    """No incidents table exists yet in M1 -- this is an honest
    capability/status placeholder, not a fabricated empty success. See
    docs/architecture/MISSION_CONTROL_M1_READONLY_API.md's M2 plan for
    what persisting real incidents would require."""
    return {
        "generated_at": _utc_now_iso(),
        "incidents": [],
        "capability": {
            "available": False,
            "reason": "no incidents table exists yet; incident persistence is planned for M2",
        },
    }
