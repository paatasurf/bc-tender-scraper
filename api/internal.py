from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from db.connection import get_session
from pipeline.internal_steps import (
    run_ai_scoring_step,
    run_arch_company_intelligence_step,
    run_company_intelligence_step,
    run_import_contract_awards_step,
    run_import_step,
)
from pipeline.runs import (
    execute_tracked_step,
    get_pipeline_run,
    list_recent_runs,
    list_runs_for_run_id,
    new_run_id,
    pipeline_run_to_dict,
    run_tracked_step,
    start_run,
)
from scraper.runners import (
    run_burnaby_permits_scraper,
    run_building_permits_scraper,
    run_commercial_scraper,
    run_federal_scraper,
    run_linkedin_scraper,
    run_merx_arch_scraper,
    run_merx_scraper,
    run_news_scraper,
    run_reddit_scraper,
    run_surrey_permits_scraper,
)

router = APIRouter(prefix="/internal", tags=["internal"])


class InternalRunRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional shared run id for grouping steps in n8n orchestration.",
    )


class InternalScrapeRunRequest(InternalRunRequest):
    days: int | None = Field(
        default=7,
        ge=1,
        le=365,
        description="Incremental window in days; set null for a full historical load.",
    )


def _require_manual_pipeline() -> None:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")


def _step_status_path(pipeline_run_id: int) -> str:
    return f"/internal/steps/{pipeline_run_id}"


def _run_status_path(run_id: str) -> str:
    return f"/internal/runs/{run_id}"


def _enqueue_step(
    background_tasks: BackgroundTasks,
    step: str,
    worker,
    run_id: str | None,
) -> dict[str, Any]:
    actual_run_id = run_id or new_run_id()
    bootstrap = get_session()
    try:
        record = start_run(bootstrap, step, actual_run_id)
        pipeline_run_id = record.id
    finally:
        bootstrap.close()

    background_tasks.add_task(
        run_tracked_step,
        step,
        worker,
        run_id=actual_run_id,
        record_id=pipeline_run_id,
    )
    return {
        "status": "started",
        "run_id": actual_run_id,
        "step": step,
        "pipeline_run_id": pipeline_run_id,
        "poll_url": _step_status_path(pipeline_run_id),
        "run_poll_url": _run_status_path(actual_run_id),
    }


def _run_step_sync(
    step: str,
    worker,
    run_id: str | None,
) -> dict[str, Any]:
    actual_run_id = run_id or new_run_id()
    result = execute_tracked_step(step, worker, run_id=actual_run_id)
    return {
        "status": result.get("status", "success"),
        "run_id": actual_run_id,
        "step": step,
        "pipeline_run_id": result.get("id"),
        "poll_url": _step_status_path(result["id"]) if result.get("id") else None,
        "run_poll_url": _run_status_path(actual_run_id),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "error": result.get("error"),
        "counts": result.get("counts", {}),
    }


@router.post("/scrape/federal")
def scrape_federal(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-federal",
        run_federal_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/merx")
def scrape_merx(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    """Refresh MERX BC open tenders and merge with existing federal rows in tenders.csv."""
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-merx",
        run_merx_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/merx-arch")
def scrape_merx_arch(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-merx-arch",
        run_merx_arch_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/commercial")
def scrape_commercial(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-commercial",
        run_commercial_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/building-permits")
def scrape_building_permits(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-building-permits",
        run_building_permits_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/surrey-permits")
def scrape_surrey_permits(
    background_tasks: BackgroundTasks,
    body: InternalScrapeRunRequest | None = None,
    sync: bool = Query(
        False,
        description="When true, run to completion and return counts instead of starting a background job.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()
    request = body or InternalScrapeRunRequest()
    worker = lambda: run_surrey_permits_scraper(days=request.days)
    if sync:
        return _run_step_sync("scrape-surrey-permits", worker, request.run_id)
    return _enqueue_step(
        background_tasks,
        "scrape-surrey-permits",
        worker,
        request.run_id,
    )


@router.post("/scrape/burnaby-permits")
def scrape_burnaby_permits(
    background_tasks: BackgroundTasks,
    body: InternalScrapeRunRequest | None = None,
    sync: bool = Query(
        False,
        description="When true, run to completion and return counts instead of starting a background job.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()
    request = body or InternalScrapeRunRequest()
    worker = lambda: run_burnaby_permits_scraper(days=request.days)
    if sync:
        return _run_step_sync("scrape-burnaby-permits", worker, request.run_id)
    return _enqueue_step(
        background_tasks,
        "scrape-burnaby-permits",
        worker,
        request.run_id,
    )


@router.post("/scrape/reddit")
def scrape_reddit(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-reddit",
        run_reddit_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/news")
def scrape_news(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-news",
        run_news_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/linkedin")
def scrape_linkedin(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-linkedin",
        run_linkedin_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/contract-awards")
def scrape_contract_awards(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    """Legacy alias for import-contract-awards."""
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "import-contract-awards",
        run_import_contract_awards_step,
        body.run_id if body else None,
    )


@router.post("/import/contract-awards")
def import_contract_awards_route(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "import-contract-awards",
        run_import_contract_awards_step,
        body.run_id if body else None,
    )


@router.post("/import")
def import_csvs(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "import-csvs",
        run_import_step,
        body.run_id if body else None,
    )


@router.post("/ai-scoring")
def ai_scoring(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
    sync: bool = Query(
        False,
        description="When true, run to completion and return counts instead of starting a background job.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()
    run_id = body.run_id if body else None
    if sync:
        return _run_step_sync("ai-scoring", run_ai_scoring_step, run_id)
    return _enqueue_step(
        background_tasks,
        "ai-scoring",
        run_ai_scoring_step,
        run_id,
    )


@router.post("/company-intelligence")
def company_intelligence(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "company-intelligence",
        run_company_intelligence_step,
        body.run_id if body else None,
    )


@router.post("/arch-company-intelligence")
def arch_company_intelligence(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "arch-company-intelligence",
        run_arch_company_intelligence_step,
        body.run_id if body else None,
    )


@router.get("/steps/{pipeline_run_id}")
def get_pipeline_step_status(pipeline_run_id: int) -> dict[str, Any]:
    """Poll a single pipeline step by database id (returned as pipeline_run_id from POST)."""
    session = get_session()
    try:
        record = get_pipeline_run(session, pipeline_run_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"No pipeline run found for id '{pipeline_run_id}'",
            )
        payload = pipeline_run_to_dict(record)
        payload["done"] = record.status in {"success", "failed", "skipped"}
        return payload
    finally:
        session.close()


@router.get("/runs")
def list_pipeline_runs(
    step: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    session = get_session()
    try:
        records = list_recent_runs(session, step=step, limit=limit)
        return {
            "total": len(records),
            "limit": limit,
            "step": step,
            "data": [pipeline_run_to_dict(record) for record in records],
        }
    finally:
        session.close()


@router.get("/runs/{run_id}")
def get_runs_for_id(run_id: str) -> dict[str, Any]:
    session = get_session()
    try:
        records = list_runs_for_run_id(session, run_id)
        if not records:
            raise HTTPException(status_code=404, detail=f"No pipeline runs found for run_id '{run_id}'")
        return {
            "run_id": run_id,
            "total": len(records),
            "data": [pipeline_run_to_dict(record) for record in records],
        }
    finally:
        session.close()
