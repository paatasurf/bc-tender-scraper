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
from pipeline.ops_read_model import (
    FRESHNESS_SOURCES,
    compute_all_source_freshness,
    get_coordinator_active_run_ids,
    get_coordinator_summary,
    get_run_detail,
    list_pipeline_runs,
)
from pipeline.scheduler import scheduler_status

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


@ops_router.get("/summary")
def ops_summary() -> dict[str, Any]:
    db_ok = check_db_connection()
    scheduler = scheduler_status()
    backend = _resolve_coordinator_backend()

    coordinator = {**_EMPTY_COORDINATOR, "backend": backend}
    if db_ok:
        ok, result = _call_with_session(
            lambda session: get_coordinator_summary(session, backend=backend)
        )
        if ok:
            coordinator = result

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
        },
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
