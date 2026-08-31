from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.connection import get_session
from db.models import PipelineRun

logger = logging.getLogger(__name__)


def new_run_id() -> str:
    return str(uuid.uuid4())


def start_run(session: Session, step: str, run_id: str | None = None) -> PipelineRun:
    record = PipelineRun(
        run_id=run_id or new_run_id(),
        step=step,
        status="running",
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def finish_run(
    session: Session,
    record_id: int,
    status: str,
    *,
    counts: dict[str, Any] | None = None,
    error: str | None = None,
) -> PipelineRun | None:
    record = session.get(PipelineRun, record_id)
    if record is None:
        return None
    record.status = status
    record.finished_at = datetime.now(timezone.utc)
    record.counts_json = json.dumps(counts or {})
    record.error = (error or "")[:4000]
    session.commit()
    session.refresh(record)
    return record


def get_pipeline_run(session: Session, pipeline_run_id: int) -> PipelineRun | None:
    return session.get(PipelineRun, pipeline_run_id)


def _resolve_status(counts: dict[str, Any]) -> str:
    """Decide pipeline_runs status from a worker's returned counts.

    Workers that report chunked-commit progress (`committed_chunks` and/or
    `write_failures` present in counts) get an honest, commit-aware status:
      - any write_failures with zero committed_chunks -> "failed" (nothing
        persisted, and something broke).
      - any write_failures with committed_chunks > 0 -> "partial_success"
        (real progress persisted, but not everything -- must not read as a
        clean success).
      - committed_chunks > 0 and no write_failures -> "success", regardless
        of how many individual records were legitimately no-ops (e.g.
        no_match candidates) -- a normal no-op record must not turn a
        successful run into "skipped".
      - no commits and no write_failures -> "skipped": the run genuinely
        did no persisting work (zero candidates, or every candidate was a
        legitimate no-op).

    Workers that don't report committed_chunks/write_failures keep the
    original, pre-existing contract unchanged: status is "skipped" iff
    counts.get("skipped") is truthy, else "success".
    """
    if "committed_chunks" in counts or "write_failures" in counts:
        committed_chunks = counts.get("committed_chunks") or 0
        write_failures = counts.get("write_failures") or 0
        if write_failures and not committed_chunks:
            return "failed"
        if write_failures and committed_chunks:
            return "partial_success"
        if committed_chunks:
            return "success"
        return "skipped"

    if counts.get("skipped"):
        return "skipped"
    return "success"


def _execute_tracked_worker(
    *,
    record_id: int,
    step: str,
    run_id: str,
    worker: Callable[[], dict[str, Any]],
) -> tuple[str, dict[str, Any], str | None]:
    """(Diagnostic-only addition -- read-only audit of the unconfirmed
    "cursor already closed" company-intelligence failures, pipeline_runs id
    770/1020: both took ~29m47s-29m53s wall-clock before failing, suspiciously
    close to db/connection.py's pool_recycle=1800s. That timing could only
    ever be checked after the fact by diffing started_at/finished_at -- for
    a "failed" run's own duration that's exact, but it says nothing about
    which internal phase/iteration the worker was on when it died, since
    worker() below is an opaque callable to this function. Appending
    elapsed wall-clock time to the persisted error text is the minimal,
    purely additive way to make that duration queryable directly from
    pipeline_runs.error without correlating log timestamps -- it changes
    nothing about control flow, retries nothing, catches nothing new, and
    the original exception text is fully preserved, only appended to."""
    start = time.monotonic()
    logger.info(
        "[Pipeline/%s] Started run_id=%s pipeline_run_id=%s", step, run_id, record_id
    )
    counts: dict[str, Any] = {}
    status = "success"
    error: str | None = None
    try:
        counts = worker()
        status = _resolve_status(counts)
    except Exception as exc:
        status = "failed"
        elapsed = time.monotonic() - start
        error = f"{exc} (after {elapsed:.1f}s)"
        logger.exception(
            "[Pipeline/%s] Failed run_id=%s pipeline_run_id=%s elapsed=%.1fs",
            step,
            run_id,
            record_id,
            elapsed,
        )
    else:
        elapsed = time.monotonic() - start
        logger.info(
            "[Pipeline/%s] Finished run_id=%s pipeline_run_id=%s status=%s "
            "elapsed=%.1fs counts=%s",
            step,
            run_id,
            record_id,
            status,
            elapsed,
            counts,
        )
    return status, counts, error


def run_tracked_step(
    step: str,
    worker: Callable[[], dict[str, Any]],
    *,
    run_id: str | None = None,
    record_id: int | None = None,
) -> None:
    """Execute a pipeline step in a background task and persist run status."""
    bootstrap = get_session()
    try:
        if record_id is None:
            record = start_run(bootstrap, step, run_id)
            record_id = record.id
            actual_run_id = record.run_id
        else:
            record = bootstrap.get(PipelineRun, record_id)
            if record is None:
                raise ValueError(f"Pipeline run {record_id} not found")
            actual_run_id = record.run_id
    finally:
        bootstrap.close()

    status, counts, error = _execute_tracked_worker(
        record_id=record_id,
        step=step,
        run_id=actual_run_id,
        worker=worker,
    )

    session = get_session()
    try:
        finish_run(session, record_id, status, counts=counts, error=error)
    finally:
        session.close()


def execute_tracked_step(
    step: str,
    worker: Callable[[], dict[str, Any]],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run a pipeline step synchronously and return the persisted run record."""
    actual_run_id = run_id or new_run_id()
    bootstrap = get_session()
    try:
        record = start_run(bootstrap, step, actual_run_id)
        record_id = record.id
    finally:
        bootstrap.close()

    status, counts, error = _execute_tracked_worker(
        record_id=record_id,
        step=step,
        run_id=actual_run_id,
        worker=worker,
    )

    session = get_session()
    try:
        record = finish_run(session, record_id, status, counts=counts, error=error)
        return pipeline_run_to_dict(record) if record is not None else {}
    finally:
        session.close()


def list_runs_for_run_id(session: Session, run_id: str) -> list[PipelineRun]:
    return list(
        session.scalars(
            select(PipelineRun)
            .where(PipelineRun.run_id == run_id)
            .order_by(PipelineRun.started_at.asc(), PipelineRun.id.asc())
        ).all()
    )


def list_recent_runs(
    session: Session,
    *,
    step: str | None = None,
    limit: int = 20,
) -> list[PipelineRun]:
    query = select(PipelineRun).order_by(
        PipelineRun.started_at.desc(), PipelineRun.id.desc()
    )
    if step:
        query = query.where(PipelineRun.step == step)
    return list(session.scalars(query.limit(limit)).all())


def pipeline_run_to_dict(record: PipelineRun) -> dict[str, Any]:
    counts: dict[str, Any]
    try:
        counts = json.loads(record.counts_json or "{}")
    except json.JSONDecodeError:
        counts = {}

    return {
        "id": record.id,
        "run_id": record.run_id,
        "step": record.step,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "error": record.error,
        "counts": counts,
    }
