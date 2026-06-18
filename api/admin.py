from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from db.connection import get_session
from pipeline.bulk_prescore import DEFAULT_BATCH_SIZE, run_bulk_prescore
from pipeline.runs import execute_tracked_step, new_run_id, run_tracked_step, start_run

router = APIRouter(tags=["admin"])


class AdminRunRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional shared run id for grouping steps in n8n orchestration.",
    )


def _require_manual_pipeline() -> None:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")


def _step_status_path(pipeline_run_id: int) -> str:
    return f"/internal/steps/{pipeline_run_id}"


def _run_status_path(run_id: str) -> str:
    return f"/internal/runs/{run_id}"


def _enqueue_bulk_prescore(
    background_tasks: BackgroundTasks,
    *,
    batch_size: int,
    max_batches: int | None,
    run_id: str | None,
) -> dict[str, Any]:
    actual_run_id = run_id or new_run_id()
    bootstrap = get_session()
    try:
        record = start_run(bootstrap, "bulk-prescore", actual_run_id)
        pipeline_run_id = record.id
    finally:
        bootstrap.close()

    def _worker() -> dict[str, Any]:
        return run_bulk_prescore(batch_size=batch_size, max_batches=max_batches)

    background_tasks.add_task(
        run_tracked_step,
        "bulk-prescore",
        _worker,
        run_id=actual_run_id,
        record_id=pipeline_run_id,
    )
    return {
        "status": "started",
        "run_id": actual_run_id,
        "step": "bulk-prescore",
        "pipeline_run_id": pipeline_run_id,
        "poll_url": _step_status_path(pipeline_run_id),
        "run_poll_url": _run_status_path(actual_run_id),
        "batch_size": batch_size,
        "max_batches": max_batches,
    }


@router.api_route("/api/admin/bulk-prescore", methods=["GET", "POST"])
def bulk_prescore(
    background_tasks: BackgroundTasks,
    body: AdminRunRequest | None = None,
    batch_size: int = Query(DEFAULT_BATCH_SIZE, ge=1, le=500),
    max_batches: int | None = Query(
        None,
        ge=1,
        description="Optional cap on batches per request (default: run until no pending companies).",
    ),
    sync: bool = Query(
        False,
        description="Blocking mode — waits until all batches finish (or max_batches is reached).",
    ),
) -> dict[str, Any]:
    """Pre-score construction companies into tender_matches using Discover hybrid matching."""
    _require_manual_pipeline()
    run_id = body.run_id if body else None

    if sync:
        result = execute_tracked_step(
            "bulk-prescore",
            lambda: run_bulk_prescore(batch_size=batch_size, max_batches=max_batches),
            run_id=run_id,
        )
        return {
            "status": result.get("status", "success"),
            "run_id": result.get("run_id"),
            "step": "bulk-prescore",
            "pipeline_run_id": result.get("id"),
            "poll_url": _step_status_path(result["id"]) if result.get("id") else None,
            "run_poll_url": _run_status_path(result["run_id"]) if result.get("run_id") else None,
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "error": result.get("error"),
            "counts": result.get("counts", {}),
            "batch_size": batch_size,
            "max_batches": max_batches,
        }

    return _enqueue_bulk_prescore(
        background_tasks,
        batch_size=batch_size,
        max_batches=max_batches,
        run_id=run_id,
    )
