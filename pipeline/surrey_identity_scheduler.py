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
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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


def run_surrey_identity_import_once(
    session,
    *,
    rows: Iterable[Mapping[str, Any]],
) -> SurreyIdentitySchedulerResult:
    """Plan, validate, and apply one atomic Surrey identity-aware import
    batch, all inside the caller's single transaction.

    Never re-raises -- every failure path rolls back and returns a
    result with ``errors=1`` instead, so a scheduled caller never has to
    guard this call itself. Never logs or returns an exception's message
    text, only its type name. Blank/invalid source identity is a stop
    condition for the whole batch (via the plan's ``invalid_rows``
    counter), never a silently-skipped row.
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
