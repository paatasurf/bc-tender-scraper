"""Surrey identity-aware scheduler integration (PR-EN1G-1, hardened).

Disabled by default via the ``ENABLE_SURREY_PERMITS_SCHEDULER`` feature
flag (``config.env.env_flag``, the same "1"/"true"/"yes" convention used
by every other flag in this repo). When the flag is off,
``surrey_scheduler_enabled()`` returns ``False`` and nothing in this
module runs: no scraper call, no import call, no DB write. The existing
daily pipeline job (``pipeline.scheduler``) is completely untouched by
this module -- Surrey is wired in as a second, independent job, not a
change to the existing one, so other scheduled sources are unaffected
either way.

When enabled, ``run_surrey_identity_import_once`` builds the *exact same*
full identity-aware plan the Class-A planner builds
(``pipeline.surrey_identity_import_canary.plan_surrey_identity_import``)
inside the caller's own transaction, before any write. If that plan's
``invalid_rows``, ``duplicate_source_rows``, or ``duplicate_risk``
counters are not all zero, the whole batch is blocked -- rolled back,
zero rows written, ``errors=1`` -- rather than silently dropping or
skipping the offending rows. Only once the plan is confirmed safe is the
same ``plan_digest`` handed to
``pipeline.surrey_identity_import_canary.apply_surrey_identity_import_full``,
which re-derives the plan once more and applies the entire batch via
``db.surrey_permit_import.upsert_surrey_permits_identity_aware`` --
never ``db.permit_import.upsert_city_permits`` (the generic importer)
and never ``scraper.surrey_permits.scrape_surrey_permits(persist=True)``
(the existing generic-importer path used by the unrelated manual
``/api/scrape/surrey-permits`` endpoint). Planning and applying share one
caller-owned transaction: the caller commits only after this function
returns a result with ``errors=0``, and rolls back on any other outcome.

No exception's ``str()`` is ever logged or returned -- only
``type(exc).__name__`` -- and the returned result carries aggregate
counters and digests only, never raw ids, PermitNumbers, applicant
names, or addresses.

(M3C) ``run_surrey_identity_import_once`` accepts an optional ``on_phase``
callback, invoked with a fixed phase name ("plan"/"validate"/"apply") at
three real boundaries -- right after a safe plan, right after the
plan is confirmed safe, and right after the caller-owned commit
succeeds ("apply" means committed, not merely "the adapter call
returned" -- if commit() itself raises, "apply" is never fired and the
exception propagates uncaught, exactly as every other exception in this
function already does). This is the ONLY hook this module exposes for
pipeline.scheduler's optional
ops_job_run telemetry (pipeline/job_run.py, gated by
ENABLE_SURREY_JOB_RUN_TELEMETRY in pipeline/scheduler.py) -- the callback
never receives the session, never touches this function's transaction,
and a failing callback is swallowed here (_safe_call_on_phase) and logged
as a fixed warning, never the callback's exception text, so telemetry can
never change this function's own success/failure/rollback behavior.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from config.env import env_flag
from pipeline.surrey_identity_import_canary import (
    apply_surrey_identity_import_full,
    plan_surrey_identity_import,
)

logger = logging.getLogger(__name__)

SURREY_SCHEDULER_FLAG = "ENABLE_SURREY_PERMITS_SCHEDULER"

__all__ = [
    "SURREY_SCHEDULER_FLAG",
    "SurreyIdentitySchedulerResult",
    "compute_result_digest",
    "map_surrey_result_to_job_run_outcome",
    "run_surrey_identity_import_once",
    "surrey_scheduler_enabled",
]


def surrey_scheduler_enabled() -> bool:
    """Read-only feature-flag check. False by default -- the flag must be
    explicitly set to one of "1"/"true"/"yes" to enable anything here."""
    return env_flag(SURREY_SCHEDULER_FLAG, default=False)


def compute_result_digest(*, plan_digest: str, updated: int, inserted: int) -> str:
    """A compact, tamper-evident receipt tying the reviewed plan_digest to
    the actual applied counts. Deliberately distinct from plan_digest --
    it changes if either the plan or the applied outcome changes, and is
    safe to publish (no raw ids/PermitNumbers/text)."""
    return hashlib.sha256(
        f"{plan_digest}:{updated}:{inserted}".encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SurreyIdentitySchedulerResult:
    """Aggregate-only observable run result. Never carries raw ids,
    PermitNumbers, applicant names, or addresses."""

    source_rows: int
    updated: int
    inserted: int
    errors: int
    plan_digest: str | None
    result_digest: str | None  # None unless the batch fully committed

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_rows": self.source_rows,
            "updated": self.updated,
            "inserted": self.inserted,
            "errors": self.errors,
            "plan_digest": self.plan_digest,
            "result_digest": self.result_digest,
        }


def _blocked(
    *, source_rows: int, plan_digest: str | None
) -> SurreyIdentitySchedulerResult:
    return SurreyIdentitySchedulerResult(
        source_rows=source_rows,
        updated=0,
        inserted=0,
        errors=1,
        plan_digest=plan_digest,
        result_digest=None,
    )


def _safe_call_on_phase(on_phase: Callable[[str], None], phase: str) -> None:
    """(M3C) Invoke an optional progress callback without ever letting it
    affect the caller. Never logs the callback's exception text -- only a
    fixed, phase-named warning -- so a telemetry-layer failure can never
    surface anything the callback happened to raise (which is out of this
    module's control) into these logs."""
    try:
        on_phase(phase)
    except Exception:
        logger.warning(
            "Surrey identity scheduler: on_phase callback failed for phase=%s", phase
        )


def map_surrey_result_to_job_run_outcome(
    result: SurreyIdentitySchedulerResult,
) -> tuple[str, dict[str, int]]:
    """(M3C) Pure mapping from a completed SurreyIdentitySchedulerResult to
    the (status, counts) pipeline.job_run.finish_job_run() should record.
    ``status`` is "success" when errors == 0, else "partial_failure" --
    "failed" is reserved for an exception escaping the surrounding
    scheduler wrapper entirely (e.g. the scrape itself failing before this
    function ever runs), never assigned here. ``counts`` is a flat dict of
    plain integers only (source_rows/inserted/updated/error_count) --
    never plan_digest, result_digest, or any text.
    """
    counts = {
        "source_rows": result.source_rows,
        "inserted": result.inserted,
        "updated": result.updated,
        "error_count": result.errors,
    }
    status = "success" if result.errors == 0 else "partial_failure"
    return status, counts


def run_surrey_identity_import_once(
    session,
    *,
    rows: Iterable[Mapping[str, Any]],
    on_phase: Callable[[str], None] | None = None,
) -> SurreyIdentitySchedulerResult:
    """Plan, validate, and apply one atomic Surrey identity-aware import
    batch, all inside the caller's single transaction.

    Never re-raises -- every failure path rolls back and returns a
    result with ``errors=1`` instead, so a scheduled caller never has to
    guard this call itself. Never logs or returns an exception's message
    text, only its type name. Blank/invalid source identity is a stop
    condition for the whole batch (via the plan's ``invalid_rows``
    counter), never a silently-skipped row.

    ``on_phase``, if given, is called with "plan"/"validate"/"apply" at
    the three real success boundaries described in the module docstring
    -- never on a blocked/failed path, and never able to affect this
    function's own outcome (see _safe_call_on_phase()). Defaults to None,
    which is a complete no-op -- existing callers are unaffected.
    """
    all_rows = list(rows)

    try:
        report = plan_surrey_identity_import(session, rows=all_rows)
    except Exception as exc:
        session.rollback()
        logger.error(
            "Surrey identity scheduler planning failed: %s", type(exc).__name__
        )
        return _blocked(source_rows=len(all_rows), plan_digest=None)

    if on_phase is not None:
        _safe_call_on_phase(on_phase, "plan")

    counts = report["counts"]
    plan_digest = report["plan_digest"]
    if (
        counts["invalid_rows"] != 0
        or counts["duplicate_source_rows"] != 0
        or counts["duplicate_risk"] != 0
    ):
        session.rollback()
        logger.error(
            "Surrey identity scheduler blocked: unsafe plan "
            "(invalid_rows=%d duplicate_source_rows=%d duplicate_risk=%d)",
            counts["invalid_rows"],
            counts["duplicate_source_rows"],
            counts["duplicate_risk"],
        )
        return _blocked(source_rows=len(all_rows), plan_digest=plan_digest)

    if on_phase is not None:
        _safe_call_on_phase(on_phase, "validate")

    try:
        result = apply_surrey_identity_import_full(
            session,
            rows=all_rows,
            expected_plan_digest=plan_digest,
        )
    except Exception as exc:
        session.rollback()
        logger.error(
            "Surrey identity scheduler apply failed, full rollback: %s",
            type(exc).__name__,
        )
        return _blocked(source_rows=len(all_rows), plan_digest=plan_digest)

    expected_updates = counts["planned_updates"]
    expected_inserts = counts["planned_inserts"]
    if (
        result.get("eligible_updates") != expected_updates
        or result.get("eligible_inserts") != expected_inserts
        or result.get("updated") != expected_updates
        or result.get("inserted") != expected_inserts
    ):
        session.rollback()
        logger.error(
            "Surrey identity scheduler blocked: applied counts did not match "
            "the reviewed plan"
        )
        return _blocked(source_rows=len(all_rows), plan_digest=plan_digest)

    session.commit()

    # The "apply" milestone means "committed", not merely "the adapter
    # call returned" -- fired strictly after a successful commit. If
    # commit() itself raises, this line is never reached: no milestone is
    # recorded, and the exception propagates uncaught out of this
    # function exactly as every other exception here would have before
    # on_phase existed -- the scheduler wrapper's own exception handling
    # (pipeline/scheduler.py) is what turns that into a terminal `failed`
    # telemetry outcome; this function does not (and must not) attempt to
    # classify or swallow a commit failure itself.
    if on_phase is not None:
        _safe_call_on_phase(on_phase, "apply")

    return SurreyIdentitySchedulerResult(
        source_rows=len(all_rows),
        updated=result["updated"],
        inserted=result["inserted"],
        errors=0,
        plan_digest=plan_digest,
        result_digest=compute_result_digest(
            plan_digest=plan_digest,
            updated=result["updated"],
            inserted=result["inserted"],
        ),
    )
