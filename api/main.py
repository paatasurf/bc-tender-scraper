from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from db.connection import get_session, init_db
from db.models import Job, Permit, RedditSignal, Tender
from pipeline.scheduler import start_scheduler, stop_scheduler

load_dotenv()


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    if data.get("scraped_at"):
        data["scraped_at"] = data["scraped_at"].isoformat()
    return data


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="BC Construction Data API",
    description="API for tenders, permits, Reddit signals, and jobs in British Columbia",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stats")
def stats() -> dict[str, int]:
    session = get_session()
    try:
        return {
            "tenders": session.scalar(select(func.count()).select_from(Tender)) or 0,
            "permits": session.scalar(select(func.count()).select_from(Permit)) or 0,
            "reddit": session.scalar(select(func.count()).select_from(RedditSignal)) or 0,
            "jobs": session.scalar(select(func.count()).select_from(Job)) or 0,
        }
    finally:
        session.close()


@app.get("/api/tenders")
def list_tenders(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(Tender)) or 0
        rows = session.scalars(select(Tender).order_by(Tender.id.desc()).offset(offset).limit(limit)).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.get("/api/permits")
def list_permits(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(Permit)) or 0
        rows = session.scalars(select(Permit).order_by(Permit.id.desc()).offset(offset).limit(limit)).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.get("/api/reddit")
def list_reddit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(RedditSignal)) or 0
        rows = session.scalars(
            select(RedditSignal).order_by(RedditSignal.upvotes.desc(), RedditSignal.id.desc()).offset(offset).limit(limit)
        ).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.get("/api/jobs")
def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(Job)) or 0
        rows = session.scalars(select(Job).order_by(Job.id.desc()).offset(offset).limit(limit)).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.post("/api/pipeline/run")
def trigger_pipeline() -> dict[str, str]:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")
    from pipeline.run import run_pipeline

    run_pipeline()
    return {"status": "completed"}
