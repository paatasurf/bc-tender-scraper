"""PostgreSQL-backed pipeline run coordinator backend (R1).

Selected by PIPELINE_COORDINATOR_BACKEND=postgres (see
pipeline/run_coordinator.py; default is "legacy"). Reads/writes durable
state in pipeline_coordinator_runs / pipeline_coordinator_steps (migration
032, applied only via
scripts/run_pipeline_coordinator_state_migration.py --apply -- see that
script and db/pipeline_coordinator_migration.py). Never auto-creates the
schema and never falls back to the legacy backend: every entry point here
preflight-checks that both tables exist (a plain read, before any lock or
mutation) and raises PipelineCoordinatorSchemaNotReadyError with a clear,
actionable message if they don't -- so switching this flag on against a
database migration 032 hasn't been applied to fails loudly and immediately
instead of hitting a raw "relation does not exist" mid-transaction or
silently doing nothing.

Why this backend exists (see the PR-0 architectural inventory): the
previous threading.Lock was process-local memory, never shared with the
subprocess.Popen-spawned run_pipeline.py child that actually runs the
scheduled pipeline, and the JSON file gave no protection across container
restarts or (if Railway ever scales out) separate replicas. This module
fixes that by making "one active run at a time" a real database constraint
(a partial unique index on pipeline_scope where status='active', migration
032) rather than an in-process check.

pipeline_coordinator_runs/pipeline_coordinator_steps are deliberately plain
SQLAlchemy Core Table objects (db/pipeline_coordinator_tables.py), not
db.models ORM classes on Base. db.connection.init_db() calls
Base.metadata.create_all(bind=engine) unconditionally on every app
startup/deploy; keeping this schema off Base.metadata is what makes "no
auto-apply at startup/deploy" actually true, not just a convention.

Scope: this coordinator has only ever tracked one pipeline -- tender
scrape through import (_SCOPE = "tender_data"). Surrey's identity-aware
import, company-intelligence, and everything else remain untracked by this
module, exactly as before; expanding coordinator scope to cover them is
explicitly out of scope for R1.

Concurrency model:
  - Every state-mutating call is one short transaction: lock the relevant
    row with SELECT ... FOR UPDATE, apply an explicit UPDATE, commit. No
    transaction is ever held open across a scraper/import call --
    coordinator functions are called at phase boundaries only, never
    wrapped around the actual work.
  - begin_run/begin_or_resume_run resolve the "only one active run" race
    with the partial unique index: a concurrent insert that loses the race
    gets a real IntegrityError from Postgres, which is caught and retried
    as a resume/conflict decision -- never a silent overwrite.
  - Each run carries a heartbeat lease (lease_expires_at), renewed by
    every state-mutating call. A run that stops making progress (crashed
    process, killed container) becomes reclaimable once its lease expires,
    so a dead run can never block every future run forever, and a live run
    can never have its state silently replaced by another run_id.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.env import get_env
from db.connection import get_session
from db.pipeline_coordinator_tables import (
    pipeline_coordinator_runs,
    pipeline_coordinator_steps,
)
from pipeline.run_coordinator_types import (
    TENDER_SCRAPE_STEPS,
    PipelineOrderError,
    PipelineRunConflictError,
    RunState,
)

__all__ = [
    "PipelineCoordinatorSchemaNotReadyError",
    "begin_run",
    "begin_or_resume_run",
    "begin_tender_scrape",
    "mark_tender_scrape_step",
    "complete_tender_scrape",
    "begin_full_scrape",
    "complete_full_scrape",
    "assert_ready_for_import",
    "begin_import",
    "complete_import",
    "finish_run",
    "get_run_state",
    "assert_import_not_before_scrape",
]

# Single coordinator scope -- see module docstring. Not exposed to callers;
# expanding this is a separate, later decision (R2+), not part of R1.
_SCOPE = "tender_data"

_DEFAULT_LEASE_TTL_SECONDS = 10800  # 3h -- generous vs. a full scrape+import run
_MAX_INSERT_RETRIES = 3

_SCHEMA_CHECK_SQL = text(
    "SELECT to_regclass('public.pipeline_coordinator_runs') IS NOT NULL "
    "AND to_regclass('public.pipeline_coordinator_steps') IS NOT NULL"
)


class PipelineCoordinatorSchemaNotReadyError(RuntimeError):
    """Raised when PIPELINE_COORDINATOR_BACKEND=postgres is selected but
    migration 032 has not been applied to the target database yet.
    Checked before any lock or mutation (see _get_checked_session()).
    Never auto-creates the schema and never falls back to the legacy
    backend -- the caller must run
    scripts/run_pipeline_coordinator_state_migration.py --apply first."""


def _schema_is_ready(session: Session) -> bool:
    return bool(session.execute(_SCHEMA_CHECK_SQL).scalar_one())


def _get_checked_session() -> Session:
    """Open a session and preflight-check the schema exists before
    returning it -- every function in this module goes through this
    instead of calling db.connection.get_session() directly, so a missing
    migration 032 fails fast and clearly, before any row lock or write."""
    session = get_session()
    try:
        ready = _schema_is_ready(session)
    except Exception:
        session.close()
        raise
    if not ready:
        session.close()
        raise PipelineCoordinatorSchemaNotReadyError(
            "PIPELINE_COORDINATOR_BACKEND=postgres is set, but migration 032 "
            "(pipeline_coordinator_runs / pipeline_coordinator_steps) has not "
            "been applied to this database yet. Run "
            "scripts/run_pipeline_coordinator_state_migration.py --dry-run, then "
            "--apply, against this DATABASE_URL before using this backend here. "
            "Refusing to auto-create the schema or fall back to the legacy "
            "backend."
        )
    return session


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _new_run_id() -> str:
    return str(uuid.uuid4())


def _lease_ttl_seconds() -> int:
    raw = get_env(
        "PIPELINE_COORDINATOR_LEASE_TTL_SECONDS", str(_DEFAULT_LEASE_TTL_SECONDS)
    )
    try:
        return max(60, int(raw))
    except ValueError:
        return _DEFAULT_LEASE_TTL_SECONDS


def _touch_lease_values() -> dict[str, Any]:
    return {
        "lease_expires_at": _utc_now() + timedelta(seconds=_lease_ttl_seconds()),
        "updated_at": _utc_now(),
    }


def _lease_expired(row: Mapping[str, Any]) -> bool:
    expires = row["lease_expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return _utc_now() >= expires


def _reclaim_stale_values(row: Mapping[str, Any]) -> dict[str, Any]:
    now = _utc_now()
    return {
        "status": "finished",
        "stale_reclaimed": True,
        "success": False,
        "error": (
            f"stale run reclaimed after lease expiry "
            f"(lease_expires_at={_iso(row['lease_expires_at'])})"
        )[:4000],
        "phase": "finished",
        "finished_at": now,
        "updated_at": now,
    }


def _completed_steps(session: Session, run_id: str) -> list[str]:
    return list(
        session.execute(
            select(pipeline_coordinator_steps.c.step)
            .where(pipeline_coordinator_steps.c.run_id == run_id)
            .order_by(pipeline_coordinator_steps.c.id.asc())
        ).scalars()
    )


def _to_run_state(session: Session, row: Mapping[str, Any]) -> RunState:
    return RunState(
        run_id=row["run_id"],
        phase=row["phase"],
        tender_scrape_started_at=_iso(row["tender_scrape_started_at"]),
        tender_scrape_finished_at=_iso(row["tender_scrape_finished_at"]),
        import_started_at=_iso(row["import_started_at"]),
        import_finished_at=_iso(row["import_finished_at"]),
        completed_tender_scrapes=_completed_steps(session, row["run_id"]),
        scrape_phase_started_at=_iso(row["scrape_phase_started_at"]),
        scrape_phase_finished_at=_iso(row["scrape_phase_finished_at"]),
        finished_at=_iso(row["finished_at"]),
        success=row["success"],
        error=row["error"],
    )


def _active_row_for_update(session: Session) -> Mapping[str, Any] | None:
    return (
        session.execute(
            select(pipeline_coordinator_runs)
            .where(
                pipeline_coordinator_runs.c.pipeline_scope == _SCOPE,
                pipeline_coordinator_runs.c.status == "active",
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def _row_by_run_id_for_update(
    session: Session, run_id: str
) -> Mapping[str, Any] | None:
    return (
        session.execute(
            select(pipeline_coordinator_runs)
            .where(pipeline_coordinator_runs.c.run_id == run_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def _latest_row_for_scope(session: Session) -> Mapping[str, Any] | None:
    return (
        session.execute(
            select(pipeline_coordinator_runs)
            .where(pipeline_coordinator_runs.c.pipeline_scope == _SCOPE)
            .order_by(pipeline_coordinator_runs.c.id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )


def _begin_or_resume(*, run_id: str | None) -> RunState:
    """Core of begin_run / begin_or_resume_run -- see module docstring for
    the concurrency model this implements."""
    last_integrity_error: IntegrityError | None = None
    for _attempt in range(_MAX_INSERT_RETRIES):
        session = _get_checked_session()
        try:
            active = _active_row_for_update(session)

            if active is not None and _lease_expired(active):
                session.execute(
                    update(pipeline_coordinator_runs)
                    .where(pipeline_coordinator_runs.c.id == active["id"])
                    .values(**_reclaim_stale_values(active))
                )
                active = None

            if active is not None:
                if run_id is not None and active["run_id"] != run_id:
                    raise PipelineRunConflictError(
                        f"Run start blocked: active run is {active['run_id']!r}, "
                        f"requested {run_id!r}. Only one active {_SCOPE!r} run "
                        "is allowed at a time."
                    )
                updated = (
                    session.execute(
                        update(pipeline_coordinator_runs)
                        .where(pipeline_coordinator_runs.c.id == active["id"])
                        .values(**_touch_lease_values())
                        .returning(pipeline_coordinator_runs)
                    )
                    .mappings()
                    .one()
                )
                state = _to_run_state(session, updated)
                session.commit()
                return state

            if run_id is not None:
                existing = (
                    session.execute(
                        select(pipeline_coordinator_runs).where(
                            pipeline_coordinator_runs.c.run_id == run_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    raise PipelineOrderError(
                        f"Run start blocked: run_id={run_id!r} already exists "
                        f"with status={existing['status']!r}. Reuse is only allowed "
                        "while a run is still active; start a new run_id instead."
                    )

            try:
                inserted = (
                    session.execute(
                        insert(pipeline_coordinator_runs)
                        .values(
                            run_id=run_id or _new_run_id(),
                            pipeline_scope=_SCOPE,
                            status="active",
                            phase="running",
                            lease_expires_at=_utc_now()
                            + timedelta(seconds=_lease_ttl_seconds()),
                        )
                        .returning(pipeline_coordinator_runs)
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as exc:
                session.rollback()
                last_integrity_error = exc
                continue

            state = _to_run_state(session, inserted)
            session.commit()
            return state
        except PipelineOrderError:
            session.rollback()
            raise
        finally:
            session.close()

    raise PipelineOrderError(
        "Run start blocked: repeated conflict creating the active run "
        f"after {_MAX_INSERT_RETRIES} attempts."
    ) from last_integrity_error


def begin_run(run_id: str) -> RunState:
    """Start (or idempotently resume) the run identified by run_id.
    Raises PipelineRunConflictError if a different run is already active
    for this scope; raises PipelineOrderError if run_id was already used
    by a run that has since finished."""
    return _begin_or_resume(run_id=run_id)


def begin_or_resume_run(run_id: str | None = None) -> RunState:
    """Like begin_run, but with run_id optional: with no run_id, reuses
    the current active run if one exists, otherwise starts a new one with
    a generated run_id."""
    return _begin_or_resume(run_id=run_id)


def _mutate_locked_run(
    run_id: str,
    build_values: Callable[[Session, Mapping[str, Any], datetime], dict[str, Any]],
) -> Mapping[str, Any]:
    """Lock the row for run_id inside one short transaction, verify it is
    still the active run with a live lease, ask `build_values` for the
    columns to change, apply them in one UPDATE, renew its lease, and
    commit.

    Raises PipelineOrderError if no row exists for run_id, if the row is
    no longer active (already finished, or already reclaimed as stale by
    a newer run's begin_run), or if its own lease has already expired --
    in that last case this reclaims the row (stale_reclaimed/finished)
    under the same lock before raising, so a late/zombie worker calling
    back in after its lease lapsed can never keep renewing itself or
    mutate a run some other worker has since taken over. A late worker
    never mutates an old/stale/finished run -- this function is the single
    enforcement point that guarantees it for every mutating coordinator
    call."""
    session = _get_checked_session()
    try:
        row = _row_by_run_id_for_update(session, run_id)
        if row is None:
            raise PipelineOrderError(f"No active run for run_id={run_id}")

        if row["status"] != "active":
            raise PipelineOrderError(
                f"Run {run_id!r} is no longer active (status={row['status']!r}); "
                "refusing to mutate a finished/reclaimed run."
            )

        if _lease_expired(row):
            session.execute(
                update(pipeline_coordinator_runs)
                .where(pipeline_coordinator_runs.c.id == row["id"])
                .values(**_reclaim_stale_values(row))
            )
            session.commit()
            raise PipelineOrderError(
                f"Run {run_id!r} lease expired (lease_expires_at="
                f"{_iso(row['lease_expires_at'])}); reclaimed as stale under this "
                "call. Refusing to apply the requested update -- start a new run "
                "instead."
            )

        now = _utc_now()
        values = build_values(session, row, now)
        values.setdefault(
            "lease_expires_at", _utc_now() + timedelta(seconds=_lease_ttl_seconds())
        )
        values["updated_at"] = now
        updated = (
            session.execute(
                update(pipeline_coordinator_runs)
                .where(pipeline_coordinator_runs.c.id == row["id"])
                .values(**values)
                .returning(pipeline_coordinator_runs)
            )
            .mappings()
            .one()
        )
        session.commit()
        return updated
    except PipelineOrderError:
        session.rollback()
        raise
    finally:
        session.close()


def begin_tender_scrape(run_id: str) -> None:
    def _build(
        _session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        values: dict[str, Any] = {"phase": "tender_scrape"}
        if row["tender_scrape_started_at"] is None:
            values["tender_scrape_started_at"] = now
        if row["scrape_phase_started_at"] is None:
            values["scrape_phase_started_at"] = now
        return values

    _mutate_locked_run(run_id, _build)


def mark_tender_scrape_step(run_id: str, step: str) -> None:
    if step not in TENDER_SCRAPE_STEPS:
        raise ValueError(f"Unknown tender scrape step: {step}")

    def _build(
        session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        session.execute(
            pg_insert(pipeline_coordinator_steps)
            .values(run_id=run_id, step=step)
            .on_conflict_do_nothing(index_elements=["run_id", "step"])
        )
        completed = _completed_steps(session, run_id)
        missing = [s for s in TENDER_SCRAPE_STEPS if s not in completed]
        values: dict[str, Any] = {}
        if not missing and row["tender_scrape_finished_at"] is None:
            values["tender_scrape_finished_at"] = now
            values["phase"] = "tender_scrape_complete"
        return values

    _mutate_locked_run(run_id, _build)


def complete_tender_scrape(run_id: str) -> None:
    def _build(
        session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        missing = [
            s for s in TENDER_SCRAPE_STEPS if s not in _completed_steps(session, run_id)
        ]
        if missing:
            raise PipelineOrderError(
                f"Tender scrape incomplete for run_id={run_id}; "
                f"missing steps: {', '.join(missing)}"
            )
        return {"tender_scrape_finished_at": now, "phase": "tender_scrape_complete"}

    _mutate_locked_run(run_id, _build)


def begin_full_scrape(run_id: str) -> None:
    def _build(
        _session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        values: dict[str, Any] = {"phase": "full_scrape"}
        if row["scrape_phase_started_at"] is None:
            values["scrape_phase_started_at"] = now
        return values

    _mutate_locked_run(run_id, _build)


def complete_full_scrape(run_id: str) -> None:
    def _build(
        _session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        values: dict[str, Any] = {"scrape_phase_finished_at": now}
        if row["phase"] != "tender_scrape_complete":
            values["phase"] = "scrape_complete"
        return values

    _mutate_locked_run(run_id, _build)


def assert_ready_for_import(run_id: str | None, *, force: bool = False) -> None:
    if force:
        return
    session = _get_checked_session()
    try:
        if run_id:
            row = (
                session.execute(
                    select(pipeline_coordinator_runs).where(
                        pipeline_coordinator_runs.c.run_id == run_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        else:
            row = _latest_row_for_scope(session)
        if row is None:
            raise PipelineOrderError(
                "Import blocked: no pipeline run has completed tender scrapers. "
                "Run the full scrape phase first."
            )
        if run_id and row["run_id"] != run_id:
            raise PipelineOrderError(
                f"Import blocked: active run is {row['run_id']!r}, requested {run_id!r}"
            )
        missing = [
            s
            for s in TENDER_SCRAPE_STEPS
            if s not in _completed_steps(session, row["run_id"])
        ]
        if missing:
            raise PipelineOrderError(
                "Import blocked: tender scrapers have not finished. "
                f"Missing steps: {', '.join(missing)}"
            )
        if not row["tender_scrape_finished_at"]:
            raise PipelineOrderError(
                "Import blocked: tender scrape phase has not been marked complete."
            )
    finally:
        session.close()


def begin_import(run_id: str) -> None:
    """The enforcement point for "import cannot start before tender scrapes
    finish" -- not just a preflight hint. Re-checks required steps and
    tender_scrape_finished_at itself, inside the same locked transaction
    _mutate_locked_run already uses to guarantee the run is active with a
    live lease. Never relies on the caller having called
    assert_ready_for_import() first (that function remains available as a
    read-only preflight for callers who want an earlier, friendlier
    rejection, e.g. manual endpoints) -- a TOCTOU gap between an external
    check and this mutation would otherwise let a stale check authorize an
    unsafe import."""

    def _build(
        session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        missing = [
            s for s in TENDER_SCRAPE_STEPS if s not in _completed_steps(session, run_id)
        ]
        if missing:
            raise PipelineOrderError(
                "Import blocked: tender scrapers have not finished. "
                f"Missing steps: {', '.join(missing)}"
            )
        if not row["tender_scrape_finished_at"]:
            raise PipelineOrderError(
                "Import blocked: tender scrape phase has not been marked complete."
            )
        return {"phase": "import", "import_started_at": now}

    _mutate_locked_run(run_id, _build)


def complete_import(run_id: str) -> None:
    def _build(
        _session: Session, row: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        return {"import_finished_at": now, "phase": "import_complete"}

    _mutate_locked_run(run_id, _build)


def finish_run(run_id: str, *, success: bool, error: str = "") -> None:
    """No-op (not an error) if run_id doesn't match any row -- mirrors the
    legacy backend's behavior, since callers invoke this from error
    cleanup paths where the run may not have been fully established."""
    session = _get_checked_session()
    try:
        row = _row_by_run_id_for_update(session, run_id)
        if row is None:
            return
        now = _utc_now()
        session.execute(
            update(pipeline_coordinator_runs)
            .where(pipeline_coordinator_runs.c.id == row["id"])
            .values(
                phase="finished",
                finished_at=now,
                success=success,
                error=error[:4000],
                status="finished",
                updated_at=now,
            )
        )
        session.commit()
    finally:
        session.close()


def get_run_state() -> RunState | None:
    """Return the most recent run's state for this scope, active or
    finished -- mirrors the legacy backend's single-slot semantics."""
    session = _get_checked_session()
    try:
        row = _latest_row_for_scope(session)
        return _to_run_state(session, row) if row is not None else None
    finally:
        session.close()


def assert_import_not_before_scrape() -> dict[str, str | None]:
    """Return ordering audit fields for logging/tests."""
    state = get_run_state()
    if state is None:
        return {
            "tender_scrape_started_at": None,
            "tender_scrape_finished_at": None,
            "import_started_at": None,
            "import_finished_at": None,
            "ordering_ok": "unknown",
        }
    ordering_ok = "unknown"
    if state.import_started_at and state.tender_scrape_finished_at:
        ordering_ok = str(state.import_started_at >= state.tender_scrape_finished_at)
    return {
        "tender_scrape_started_at": state.tender_scrape_started_at,
        "tender_scrape_finished_at": state.tender_scrape_finished_at,
        "import_started_at": state.import_started_at,
        "import_finished_at": state.import_finished_at,
        "ordering_ok": ordering_ok,
    }
