from __future__ import annotations

import config.env  # noqa: F401  # load env before pipeline imports

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Float, func, select

from datetime import datetime

from db.connection import get_session, init_db
from db.models import (
    ArchCompany,
    ArchTender,
    CommercialTender,
    Company,
    Job,
    Permit,
    RedditSignal,
    Tender,
)
from config.env import get_anthropic_api_key
from pipeline.scheduler import start_scheduler, stop_scheduler


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
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
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "anthropic_api_key_configured": bool(get_anthropic_api_key()),
    }


@app.get("/api/stats")
def stats() -> dict[str, int]:
    session = get_session()
    try:
        return {
            "tenders": session.scalar(select(func.count()).select_from(Tender)) or 0,
            "permits": session.scalar(select(func.count()).select_from(Permit)) or 0,
            "reddit": session.scalar(select(func.count()).select_from(RedditSignal)) or 0,
            "jobs": session.scalar(select(func.count()).select_from(Job)) or 0,
            "arch_tenders": session.scalar(select(func.count()).select_from(ArchTender)) or 0,
            "commercial_tenders": session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
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


@app.get("/api/contract-awards")
def list_contract_awards(
    limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    """Top applicants by aggregated Vancouver building permit project value."""
    session = get_session()
    try:
        value_sum = func.sum(func.cast(func.nullif(Permit.project_value, ""), Float))
        rows = session.execute(
            select(
                Permit.applicant.label("company"),
                value_sum.label("total_value"),
                func.count(Permit.id).label("permit_count"),
            )
            .where(Permit.applicant != "", Permit.applicant.isnot(None))
            .group_by(Permit.applicant)
            .having(value_sum > 0)
            .order_by(value_sum.desc())
            .limit(limit)
        ).all()

        data = [
            {
                "company": row.company,
                "contract": f"{row.permit_count} permits",
                "value": float(row.total_value or 0),
                "date": "",
            }
            for row in rows
        ]
        return {"total": len(data), "limit": limit, "offset": 0, "data": data}
    finally:
        session.close()


@app.get("/api/arch-tenders")
def list_arch_tenders(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(ArchTender)) or 0
        rows = session.scalars(
            select(ArchTender).order_by(ArchTender.id.desc()).offset(offset).limit(limit)
        ).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.get("/api/commercial-tenders")
def list_commercial_tenders(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(CommercialTender)) or 0
        rows = session.scalars(
            select(CommercialTender).order_by(CommercialTender.id.desc()).offset(offset).limit(limit)
        ).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


def _find_company(session, name: str) -> Company | None:
    return session.scalars(
        select(Company).where(func.lower(Company.name) == name.strip().lower())
    ).first()


def _find_tender(session, tender_id: int):
    for model in (ArchTender, CommercialTender, Tender):
        tender = session.get(model, tender_id)
        if tender is not None:
            return tender
    return None


@app.get("/api/companies")
def list_companies(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=300),
) -> dict[str, Any]:
    session = get_session()
    try:
        query = select(Company)
        count_query = select(func.count()).select_from(Company)
        if search.strip():
            pattern = f"%{search.strip()}%"
            query = query.where(Company.name.ilike(pattern))
            count_query = count_query.where(Company.name.ilike(pattern))

        total = session.scalar(count_query) or 0
        rows = session.scalars(
            query.order_by(Company.total_value.desc()).offset(offset).limit(limit)
        ).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.get("/api/companies/{name}")
def get_company(name: str) -> dict[str, Any]:
    session = get_session()
    try:
        company = _find_company(session, name)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Company '{name}' not found")
        return _row_to_dict(company)
    finally:
        session.close()


@app.get("/api/companies/{name}/tender-match/{tender_id}")
def company_tender_match(name: str, tender_id: int) -> dict[str, Any]:
    from pipeline.company_intelligence import match_company_to_tender

    if not get_anthropic_api_key():
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    session = get_session()
    try:
        company = _find_company(session, name)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Company '{name}' not found")

        tender = _find_tender(session, tender_id)
        if tender is None:
            raise HTTPException(status_code=404, detail=f"Tender {tender_id} not found")

        try:
            match = match_company_to_tender(company, tender)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"AI match failed: {exc}") from exc

        return {
            "company": company.name,
            "tender_id": tender.id,
            "tender_title": tender.title,
            "tender_source": tender.__tablename__,
            **match,
        }
    finally:
        session.close()


def _find_arch_company(session, name: str) -> ArchCompany | None:
    return session.scalars(
        select(ArchCompany).where(func.lower(ArchCompany.name) == name.strip().lower())
    ).first()


@app.get("/api/arch-companies")
def list_arch_companies(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=300),
) -> dict[str, Any]:
    session = get_session()
    try:
        query = select(ArchCompany)
        count_query = select(func.count()).select_from(ArchCompany)
        if search.strip():
            pattern = f"%{search.strip()}%"
            query = query.where(ArchCompany.name.ilike(pattern))
            count_query = count_query.where(ArchCompany.name.ilike(pattern))

        total = session.scalar(count_query) or 0
        rows = session.scalars(
            query.order_by(ArchCompany.total_value.desc()).offset(offset).limit(limit)
        ).all()
        return {"total": total, "limit": limit, "offset": offset, "data": [_row_to_dict(row) for row in rows]}
    finally:
        session.close()


@app.get("/api/arch-companies/{name}")
def get_arch_company(name: str) -> dict[str, Any]:
    session = get_session()
    try:
        company = _find_arch_company(session, name)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Architecture firm '{name}' not found")
        return _row_to_dict(company)
    finally:
        session.close()


@app.get("/api/arch-companies/{name}/tender-match/{tender_id}")
def arch_company_tender_match(name: str, tender_id: int) -> dict[str, Any]:
    from pipeline.arch_company_intelligence import match_arch_company_to_tender

    if not get_anthropic_api_key():
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    session = get_session()
    try:
        company = _find_arch_company(session, name)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Architecture firm '{name}' not found")

        tender = session.get(ArchTender, tender_id)
        if tender is None:
            raise HTTPException(status_code=404, detail=f"Arch tender {tender_id} not found")

        try:
            match = match_arch_company_to_tender(company, tender)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"AI match failed: {exc}") from exc

        return {
            "company": company.name,
            "tender_id": tender.id,
            "tender_title": tender.title,
            "tender_source": tender.__tablename__,
            **match,
        }
    finally:
        session.close()


@app.get("/api/pipeline/status")
def pipeline_status() -> dict[str, str | int | bool]:
    from pipeline.executor import pipeline_status as get_status

    return get_status()


@app.post("/api/pipeline/run")
def trigger_pipeline(background_tasks: BackgroundTasks) -> dict[str, str]:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")
    from pipeline.run import run_pipeline

    background_tasks.add_task(run_pipeline)
    return {"status": "started"}
