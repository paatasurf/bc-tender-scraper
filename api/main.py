import config.env  # noqa: F401  # load env before pipeline imports

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Float, func, select
from sqlalchemy.exc import DBAPIError, OperationalError

from datetime import datetime

from db.connection import (
    check_db_connection,
    get_db_init_status,
    get_session,
    is_transient_db_error,
    start_init_db_background,
)
from pipeline.opportunity_discovery import ARCHITECTURE_DEFAULT_MIN_SCORE, CONSTRUCTION_DEFAULT_MIN_SCORE
from db.models import (
    ArchCompany,
    ArchTender,
    CommercialTender,
    Company,
    ContractAward,
    Job,
    LinkedInSignal,
    NewsSignal,
    Permit,
    RedditSignal,
    Tender,
)
from api.internal import router as internal_router
from config.env import get_anthropic_api_key
from pipeline.executor import pipeline_status as get_pipeline_runtime_status
from pipeline.scheduler import scheduler_status, start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bind HTTP port immediately; schema init runs in a background thread.
    start_scheduler()
    start_init_db_background()
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

app.include_router(internal_router)


@app.exception_handler(OperationalError)
@app.exception_handler(DBAPIError)
def database_unavailable_handler(_request: Request, exc: Exception) -> JSONResponse:
    if is_transient_db_error(exc):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Database is temporarily unavailable (recovery or connection issue). Retry shortly.",
            },
            headers={"Retry-After": "30"},
        )
    return JSONResponse(status_code=500, content={"detail": "Database error"})


@app.get("/api/health")
def health() -> dict[str, str | bool | None]:
    db_ok = check_db_connection()
    scheduler = scheduler_status()
    init_status = get_db_init_status()
    return {
        "status": "ok" if db_ok else "degraded",
        "database_connected": db_ok,
        "anthropic_api_key_configured": bool(get_anthropic_api_key()),
        "scheduler_enabled": bool(scheduler.get("enabled")),
        "scheduler_running": bool(scheduler.get("running")),
        "db_init_status": init_status["status"],
        "db_init_error": init_status["error"],
    }


@app.get("/api/stats")
def stats() -> dict[str, int]:
    session = get_session()
    try:
        return {
            "tenders": session.scalar(select(func.count()).select_from(Tender)) or 0,
            "permits": session.scalar(select(func.count()).select_from(Permit)) or 0,
            "reddit": session.scalar(select(func.count()).select_from(RedditSignal)) or 0,
            "news": session.scalar(select(func.count()).select_from(NewsSignal)) or 0,
            "linkedin": session.scalar(select(func.count()).select_from(LinkedInSignal)) or 0,
            "jobs": session.scalar(select(func.count()).select_from(Job)) or 0,
            "arch_tenders": session.scalar(select(func.count()).select_from(ArchTender)) or 0,
            "commercial_tenders": session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
            "contract_awards": session.scalar(select(func.count()).select_from(ContractAward)) or 0,
            "contract_awards_matched": session.scalar(
                select(func.count()).select_from(ContractAward).where(ContractAward.company_id.isnot(None))
            )
            or 0,
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


def _reddit_to_signal(row: RedditSignal) -> dict[str, Any]:
    return {
        "source": "REDDIT",
        "title": row.title,
        "text": row.text,
        "url": row.url,
        "date": row.date,
        "upvotes": row.upvotes,
        "subreddit": row.subreddit or "",
        "publisher": "",
        "author": "",
    }


def _news_to_signal(row: NewsSignal) -> dict[str, Any]:
    return {
        "source": "NEWS",
        "title": row.title,
        "text": row.text,
        "url": row.url,
        "date": row.date,
        "upvotes": 0,
        "subreddit": "",
        "publisher": row.publisher,
        "author": "",
    }


def _linkedin_to_signal(row: LinkedInSignal) -> dict[str, Any]:
    return {
        "source": "LINKEDIN",
        "title": row.title,
        "text": row.content,
        "url": row.url,
        "date": row.date,
        "upvotes": row.likes_count,
        "subreddit": "",
        "publisher": "",
        "author": row.author,
    }


@app.get("/api/signals")
def list_signals(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    session = get_session()
    try:
        reddit_rows = session.scalars(select(RedditSignal)).all()
        news_rows = session.scalars(select(NewsSignal)).all()
        linkedin_rows = session.scalars(select(LinkedInSignal)).all()

        merged: list[dict[str, Any]] = []
        merged.extend(_reddit_to_signal(row) for row in reddit_rows)
        merged.extend(_news_to_signal(row) for row in news_rows)
        merged.extend(_linkedin_to_signal(row) for row in linkedin_rows)

        merged.sort(key=lambda item: (-item["upvotes"], item["date"]), reverse=False)
        total = len(merged)
        page = merged[offset : offset + limit]
        return {"total": total, "limit": limit, "offset": offset, "data": page}
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
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    source: str | None = Query(None, max_length=40),
    matched: bool | None = Query(None, description="Filter by company match status"),
    company_id: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    session = get_session()
    try:
        query = select(ContractAward)
        if source:
            query = query.where(ContractAward.source == source)
        if matched is True:
            query = query.where(ContractAward.company_id.isnot(None))
        elif matched is False:
            query = query.where(ContractAward.company_id.is_(None))
        if company_id is not None:
            query = query.where(ContractAward.company_id == company_id)

        count_query = select(func.count()).select_from(ContractAward)
        if source:
            count_query = count_query.where(ContractAward.source == source)
        if matched is True:
            count_query = count_query.where(ContractAward.company_id.isnot(None))
        elif matched is False:
            count_query = count_query.where(ContractAward.company_id.is_(None))
        if company_id is not None:
            count_query = count_query.where(ContractAward.company_id == company_id)
        total = session.scalar(count_query) or 0
        rows = session.scalars(
            query.order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()

        company_names: dict[int, str] = {}
        company_ids = {row.company_id for row in rows if row.company_id is not None}
        if company_ids:
            for cid, name in session.execute(
                select(Company.id, Company.name).where(Company.id.in_(company_ids))
            ).all():
                company_names[cid] = name

        data = []
        for row in rows:
            item = _row_to_dict(row)
            item["matched_company_name"] = company_names.get(row.company_id or 0, "")
            data.append(item)

        return {"total": total, "limit": limit, "offset": offset, "data": data}
    finally:
        session.close()


@app.get("/api/contract-awards/summary")
def contract_awards_summary() -> dict[str, Any]:
    session = get_session()
    try:
        total = session.scalar(select(func.count()).select_from(ContractAward)) or 0
        matched = (
            session.scalar(
                select(func.count()).select_from(ContractAward).where(ContractAward.company_id.isnot(None))
            )
            or 0
        )
        by_source = session.execute(
            select(ContractAward.source, func.count())
            .group_by(ContractAward.source)
            .order_by(func.count().desc())
        ).all()
        value_sum = session.scalar(select(func.coalesce(func.sum(ContractAward.award_value), 0.0))) or 0.0
        return {
            "total_awards": total,
            "matched_awards": matched,
            "match_rate": round(matched / total * 100, 1) if total else 0.0,
            "total_award_value": float(value_sum),
            "by_source": {source: count for source, count in by_source},
        }
    finally:
        session.close()


@app.get("/api/contract-awards/top-vendors")
def contract_awards_top_vendors(
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    session = get_session()
    try:
        rows = session.execute(
            select(
                ContractAward.winner_company,
                func.count(ContractAward.id).label("award_count"),
                func.coalesce(func.sum(ContractAward.award_value), 0.0).label("total_value"),
                func.max(ContractAward.company_id).label("company_id"),
            )
            .group_by(ContractAward.winner_company)
            .order_by(func.coalesce(func.sum(ContractAward.award_value), 0.0).desc())
            .limit(limit)
        ).all()

        data = [
            {
                "vendor": row.winner_company,
                "award_count": row.award_count,
                "total_value": float(row.total_value or 0),
                "company_id": row.company_id,
                "matched": row.company_id is not None,
            }
            for row in rows
        ]
        return {"total": len(data), "limit": limit, "data": data}
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


@app.get("/api/companies/{company_id}/opportunities")
def company_opportunities(
    company_id: int,
    kind: Literal["construction", "architecture"] = Query("construction"),
    min_score: int = Query(CONSTRUCTION_DEFAULT_MIN_SCORE, ge=0, le=100),
    limit: int = Query(15, ge=1, le=50),
) -> dict[str, Any]:
    from pipeline.opportunity_discovery import discover_opportunities

    started = time.perf_counter()
    try:
        result = discover_opportunities(
            company_id=company_id,
            kind=kind,
            min_score=min_score,
            limit=limit,
        )
        print(
            f"[API] company_opportunities company_id={company_id} kind={kind} "
            f"total={time.perf_counter() - started:.2f}s"
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _tender_response_fields(tender: Any) -> dict[str, Any]:
    """Common tender fields for API responses."""
    deadline = getattr(tender, "closing_date", None) or getattr(tender, "deadline", "") or ""
    value_raw = getattr(tender, "estimated_value", None) or getattr(tender, "value", "") or ""
    budget = getattr(tender, "ai_budget_estimate", "") or ""
    return {
        "tender_title": tender.title,
        "tender_url": getattr(tender, "url", "") or "",
        "tender_deadline": deadline,
        "tender_value": str(value_raw),
        "tender_budget_estimate": budget,
        "tender_category": getattr(tender, "category", "") or "",
        "tender_source": tender.__tablename__,
    }


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
            **_tender_response_fields(tender),
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


@app.get("/api/arch-companies/{company_id}/opportunities")
def arch_company_opportunities(
    company_id: int,
    min_score: int = Query(ARCHITECTURE_DEFAULT_MIN_SCORE, ge=0, le=100),
    limit: int = Query(15, ge=1, le=50),
) -> dict[str, Any]:
    from pipeline.opportunity_discovery import discover_opportunities

    started = time.perf_counter()
    try:
        result = discover_opportunities(
            company_id=company_id,
            kind="architecture",
            min_score=min_score,
            limit=limit,
        )
        print(
            f"[API] arch_company_opportunities company_id={company_id} "
            f"total={time.perf_counter() - started:.2f}s"
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        logger.exception(
            "arch_company_opportunities failed company_id=%s min_score=%s limit=%s",
            company_id,
            min_score,
            limit,
        )
        raise


@app.get("/api/companies/{company_id}/bd-intelligence")
def company_bd_intelligence(
    company_id: int,
    kind: Literal["construction", "architecture"] = Query("construction"),
    active_limit: int = Query(5, ge=0, le=10),
    pipeline_limit: int = Query(5, ge=0, le=10),
    intel_limit: int = Query(5, ge=0, le=10),
    relationship_limit: int = Query(3, ge=0, le=10),
    growth_limit: int = Query(2, ge=0, le=5),
    refresh_profile: bool = Query(False),
    include_rejections: bool = Query(False),
    min_bps: int | None = Query(None, ge=50, le=95),
) -> dict[str, Any]:
    from pipeline.bd_recommendations import recommend_bd_intelligence

    session = get_session()
    try:
        try:
            return recommend_bd_intelligence(
                session,
                company_id=company_id,
                kind=kind,
                active_limit=active_limit,
                pipeline_limit=pipeline_limit,
                intel_limit=intel_limit,
                relationship_limit=relationship_limit,
                growth_limit=growth_limit,
                refresh_profile=refresh_profile,
                include_rejections=include_rejections,
                min_bps=min_bps,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/companies/{company_id}/competitive-intelligence")
@app.get("/api/companies/id/{company_id}/competitive-intelligence")
def company_competitive_intelligence(
    company_id: int,
    peer_limit: int = Query(5, ge=1, le=10),
    refresh_cip: bool = Query(False),
) -> dict[str, Any]:
    from pipeline.competitive_intel.service import get_competitive_intelligence

    session = get_session()
    try:
        try:
            return get_competitive_intelligence(
                session,
                company_id=company_id,
                kind="construction",
                peer_limit=peer_limit,
                refresh_cip=refresh_cip,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/arch-companies/{company_id}/competitive-intelligence")
@app.get("/api/arch-companies/id/{company_id}/competitive-intelligence")
def arch_company_competitive_intelligence(
    company_id: int,
    peer_limit: int = Query(5, ge=1, le=10),
    refresh_cip: bool = Query(False),
) -> dict[str, Any]:
    from pipeline.competitive_intel.service import get_competitive_intelligence

    session = get_session()
    try:
        try:
            return get_competitive_intelligence(
                session,
                company_id=company_id,
                kind="architecture",
                peer_limit=peer_limit,
                refresh_cip=refresh_cip,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/competitive-intelligence/missed-opportunities")
def competitive_intelligence_missed_opportunities(
    company_id: int = Query(..., ge=1),
    peer_limit: int = Query(5, ge=3, le=5),
) -> dict[str, Any]:
    from pipeline.competitive_intel.tender_activity import get_missed_opportunities

    session = get_session()
    try:
        try:
            return get_missed_opportunities(
                session,
                company_id=company_id,
                kind="construction",
                peer_limit=peer_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/competitive-intelligence/competitor-activity")
def competitive_intelligence_competitor_activity(
    company_id: int = Query(..., ge=1),
    peer_limit: int = Query(5, ge=3, le=5),
) -> dict[str, Any]:
    from pipeline.competitive_intel.tender_activity import get_competitor_tender_activity

    session = get_session()
    try:
        try:
            return get_competitor_tender_activity(
                session,
                company_id=company_id,
                kind="construction",
                peer_limit=peer_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/companies/{company_id}/capability-profile")
def company_capability_profile(
    company_id: int,
    kind: Literal["construction", "architecture"] = Query("construction"),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    from pipeline.cip_builder import get_cip

    session = get_session()
    try:
        try:
            cip = get_cip(session, company_id=company_id, kind=kind, refresh=refresh)
            return cip.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/arch-companies/{company_id}/bd-intelligence")
def arch_company_bd_intelligence(
    company_id: int,
    active_limit: int = Query(5, ge=0, le=10),
    pipeline_limit: int = Query(5, ge=0, le=10),
    intel_limit: int = Query(0, ge=0, le=10),
    relationship_limit: int = Query(3, ge=0, le=10),
    growth_limit: int = Query(2, ge=0, le=5),
    refresh_profile: bool = Query(False),
    include_rejections: bool = Query(False),
    min_bps: int | None = Query(None, ge=50, le=95),
) -> dict[str, Any]:
    from pipeline.bd_recommendations import recommend_bd_intelligence

    session = get_session()
    try:
        try:
            return recommend_bd_intelligence(
                session,
                company_id=company_id,
                kind="architecture",
                active_limit=active_limit,
                pipeline_limit=pipeline_limit,
                intel_limit=intel_limit,
                relationship_limit=relationship_limit,
                growth_limit=growth_limit,
                refresh_profile=refresh_profile,
                include_rejections=include_rejections,
                min_bps=min_bps,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()


@app.get("/api/arch-companies/{company_id}/capability-profile")
def arch_company_capability_profile(
    company_id: int,
    refresh: bool = Query(False),
) -> dict[str, Any]:
    from pipeline.cip_builder import get_cip

    session = get_session()
    try:
        try:
            cip = get_cip(session, company_id=company_id, kind="architecture", refresh=refresh)
            return cip.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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

        tender = _find_tender(session, tender_id)
        if tender is None:
            raise HTTPException(status_code=404, detail=f"Tender {tender_id} not found")

        try:
            match = match_arch_company_to_tender(company, tender)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"AI match failed: {exc}") from exc

        return {
            "company": company.name,
            "tender_id": tender.id,
            **_tender_response_fields(tender),
            **match,
        }
    finally:
        session.close()


@app.get("/api/scrape/surrey-permits")
def scrape_surrey_permits_route(
    days: int | None = Query(
        None,
        ge=1,
        le=365,
        description="Incremental window in days; omit for full historical load",
    ),
) -> dict[str, Any]:
    from scraper.surrey_permits import scrape_surrey_permits

    try:
        return scrape_surrey_permits(days=days, persist=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Surrey permits scrape failed: {exc}") from exc


@app.get("/api/pipeline/status")
def pipeline_status() -> dict[str, Any]:
    runtime = get_pipeline_runtime_status()
    scheduler = scheduler_status()
    return {
        **runtime,
        "scheduler": scheduler,
        "daily_job": "daily_scrape_import → run_pipeline.py",
    }


def _run_ai_scoring() -> None:
    from pipeline.ai_scoring import score_unscored_tenders

    session = get_session()
    try:
        try:
            results = score_unscored_tenders(session)
        except Exception as exc:
            print(f"[AI Scoring] Manual run failed: {exc}")
            return

        print(f"[AI Scoring] Manual run complete: {results}")
    finally:
        session.close()


def _run_arch_google_places() -> None:
    from pipeline.arch_company_intelligence import enrich_arch_companies_google
    from pipeline.scrape_arch_companies_google import scrape_arch_companies_google

    session = get_session()
    try:
        try:
            scraped = scrape_arch_companies_google(session)
        except Exception as exc:
            print(f"[ArchCompanies] Google Places scrape failed: {exc}")
            scraped = 0

        try:
            enriched = enrich_arch_companies_google(session)
        except Exception as exc:
            print(f"[ArchCompanies] Google enrichment failed: {exc}")
            enriched = 0

        print(
            f"[ArchCompanies] Google Places manual run complete: "
            f"{scraped} scraped, {enriched} enriched"
        )
    finally:
        session.close()


@app.post("/api/pipeline/run")
def trigger_pipeline(background_tasks: BackgroundTasks) -> dict[str, str]:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")
    from pipeline.run import run_pipeline

    background_tasks.add_task(run_pipeline)
    return {"status": "started"}


@app.post("/api/pipeline/run-google-places")
def trigger_google_places(background_tasks: BackgroundTasks) -> dict[str, str]:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")

    background_tasks.add_task(_run_arch_google_places)
    return {"status": "started"}


@app.post("/api/pipeline/run-ai-scoring")
def trigger_ai_scoring(background_tasks: BackgroundTasks) -> dict[str, str]:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")

    background_tasks.add_task(_run_ai_scoring)
    return {"status": "started"}


class AIMatchingRequest(BaseModel):
    company_id: int | None = Field(
        default=None,
        description="companies.id or arch_companies.id. Required when sync=true.",
    )
    kind: str = Field(
        default="architecture",
        description="Company type: architecture or construction.",
    )
    max_companies: int = Field(default=10, ge=1, le=100)
    max_tenders: int = Field(default=50, ge=1, le=500)
    sync: bool = Field(
        default=False,
        description="When true with company_id, run matcher+scorer inline and return ranked matches.",
    )
    min_score: int = Field(default=65, ge=0, le=100)
    limit: int = Field(default=5, ge=1, le=50)


def _run_ai_matching_task(
    company_id: int | None,
    max_companies: int,
    max_tenders: int,
) -> None:
    from pipeline.ai_matching import run_ai_matching

    session = get_session()
    try:
        results = run_ai_matching(
            session,
            company_id=company_id,
            max_companies=max_companies,
            max_tenders=max_tenders,
        )
        print(f"[AI Matching] Background run complete: {results}")
    except Exception as exc:
        print(f"[AI Matching] Background run failed: {exc}")
    finally:
        session.close()


@app.post("/api/ai-matching")
def trigger_ai_matching(
    background_tasks: BackgroundTasks,
    body: AIMatchingRequest | None = None,
) -> dict[str, Any]:
    request = body or AIMatchingRequest()
    kind = (request.kind or "architecture").strip().lower()
    architecture_sync = request.sync and kind == "architecture"
    construction_sync = request.sync and kind == "construction"

    if not architecture_sync and not construction_sync and not get_anthropic_api_key():
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    if request.sync:
        if request.company_id is None:
            raise HTTPException(
                status_code=400,
                detail="company_id is required when sync=true",
            )

        from pipeline.ai_matching import run_unified_ai_matching_sync

        if kind not in {"architecture", "construction"}:
            raise HTTPException(
                status_code=400,
                detail="kind must be 'architecture' or 'construction'",
            )

        session = get_session()
        try:
            try:
                matches = run_unified_ai_matching_sync(
                    session,
                    company_id=request.company_id,
                    kind=kind,
                    max_tenders=request.max_tenders,
                    min_score=request.min_score,
                    limit=request.limit,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"AI matching failed: {exc}") from exc

            return {
                "status": "complete",
                "kind": kind,
                "company_id": request.company_id,
                "min_score": request.min_score,
                "limit": request.limit,
                "matches": matches,
            }
        finally:
            session.close()

    background_tasks.add_task(
        _run_ai_matching_task,
        request.company_id,
        request.max_companies,
        request.max_tenders,
    )
    return {
        "status": "started",
        "company_id": request.company_id,
        "max_companies": request.max_companies,
        "max_tenders": request.max_tenders,
    }
