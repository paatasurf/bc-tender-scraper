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
    run_import_step,
)
from pipeline.runs import (
    list_recent_runs,
    list_runs_for_run_id,
    new_run_id,
    pipeline_run_to_dict,
    run_tracked_step,
)
from scraper.runners import (
    run_building_permits_scraper,
    run_commercial_scraper,
    run_contract_awards_scraper,
    run_federal_scraper,
    run_linkedin_scraper,
    run_merx_arch_scraper,
    run_news_scraper,
    run_reddit_scraper,
)

router = APIRouter(prefix="/internal", tags=["internal"])


class InternalRunRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional shared run id for grouping steps in n8n orchestration.",
    )


def _require_manual_pipeline() -> None:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")


def _enqueue_step(
    background_tasks: BackgroundTasks,
    step: str,
    worker,
    run_id: str | None,
) -> dict[str, str]:
    actual_run_id = run_id or new_run_id()
    background_tasks.add_task(run_tracked_step, step, worker, run_id=actual_run_id)
    return {"status": "started", "run_id": actual_run_id, "step": step}


@router.post("/scrape/federal")
def scrape_federal(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, str]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-federal",
        run_federal_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/merx-arch")
def scrape_merx_arch(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, str]:
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
) -> dict[str, str]:
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
) -> dict[str, str]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-building-permits",
        run_building_permits_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/reddit")
def scrape_reddit(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, str]:
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
) -> dict[str, str]:
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
) -> dict[str, str]:
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
) -> dict[str, str]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-contract-awards",
        run_contract_awards_scraper,
        body.run_id if body else None,
    )


@router.post("/import")
def import_csvs(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, str]:
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
) -> dict[str, str]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "ai-scoring",
        run_ai_scoring_step,
        body.run_id if body else None,
    )


@router.post("/company-intelligence")
def company_intelligence(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, str]:
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
) -> dict[str, str]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "arch-company-intelligence",
        run_arch_company_intelligence_step,
        body.run_id if body else None,
    )


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
