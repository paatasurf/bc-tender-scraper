"""Win/loss outcome tracking API (Phase X.1.5)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from db.connection import get_session
from db.models import TenderOutcome

router = APIRouter(prefix="/api/win-loss", tags=["win-loss"])

OutcomeValue = Literal["won", "lost", "withdrew", "pending"]


class RecordOutcomeRequest(BaseModel):
    company_id: int = Field(..., gt=0)
    tender_id: str = Field(..., min_length=1, max_length=255)
    tender_title: str | None = Field(None, max_length=500)
    outcome: OutcomeValue
    bid_amount: float | None = None
    award_amount: float | None = None
    notes: str | None = None


def _to_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


def outcome_to_dict(row: TenderOutcome) -> dict[str, Any]:
    recorded_at = row.recorded_at
    if isinstance(recorded_at, datetime):
        recorded_at = recorded_at.isoformat()
    return {
        "id": row.id,
        "company_id": row.company_id,
        "tender_id": row.tender_id,
        "tender_title": row.tender_title,
        "outcome": row.outcome,
        "bid_amount": _to_float(row.bid_amount),
        "award_amount": _to_float(row.award_amount),
        "notes": row.notes,
        "recorded_at": recorded_at,
    }


def compute_win_loss_stats(rows: list[TenderOutcome]) -> dict[str, Any]:
    total_bids = len(rows)
    won = sum(1 for row in rows if row.outcome == "won")
    lost = sum(1 for row in rows if row.outcome == "lost")
    withdrew = sum(1 for row in rows if row.outcome == "withdrew")
    pending = sum(1 for row in rows if row.outcome == "pending")

    decided = won + lost
    win_rate = round(won / decided, 4) if decided else 0.0

    bid_amounts = [_to_float(row.bid_amount) for row in rows if row.bid_amount is not None]
    avg_bid_amount = round(sum(bid_amounts) / len(bid_amounts), 2) if bid_amounts else 0.0

    total_won_value = round(
        sum(_to_float(row.award_amount) or 0.0 for row in rows if row.outcome == "won"),
        2,
    )

    return {
        "total_bids": total_bids,
        "won": won,
        "lost": lost,
        "withdrew": withdrew,
        "pending": pending,
        "win_rate": win_rate,
        "avg_bid_amount": avg_bid_amount,
        "total_won_value": total_won_value,
    }


def upsert_outcome(session, payload: RecordOutcomeRequest) -> TenderOutcome:
    existing = session.scalar(
        select(TenderOutcome).where(
            TenderOutcome.company_id == payload.company_id,
            TenderOutcome.tender_id == payload.tender_id,
        )
    )
    if existing is None:
        row = TenderOutcome(
            company_id=payload.company_id,
            tender_id=payload.tender_id,
            tender_title=payload.tender_title,
            outcome=payload.outcome,
            bid_amount=payload.bid_amount,
            award_amount=payload.award_amount,
            notes=payload.notes,
        )
        session.add(row)
        session.flush()
        return row

    existing.tender_title = payload.tender_title
    existing.outcome = payload.outcome
    existing.bid_amount = payload.bid_amount
    existing.award_amount = payload.award_amount
    existing.notes = payload.notes
    existing.recorded_at = datetime.now(timezone.utc)
    session.flush()
    return existing


@router.post("")
def record_outcome(body: RecordOutcomeRequest) -> dict[str, Any]:
    """Record or update a tender outcome (upsert on company_id + tender_id)."""
    session = get_session()
    try:
        row = upsert_outcome(session, body)
        session.commit()
        session.refresh(row)
        return outcome_to_dict(row)
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to record outcome: {exc}") from exc
    finally:
        session.close()


@router.get("/{company_id}")
def list_outcomes(company_id: int) -> dict[str, Any]:
    """Full outcome history for a company, most recent first."""
    if company_id <= 0:
        raise HTTPException(status_code=400, detail="company_id must be positive")

    session = get_session()
    try:
        rows = session.scalars(
            select(TenderOutcome)
            .where(TenderOutcome.company_id == company_id)
            .order_by(desc(TenderOutcome.recorded_at), desc(TenderOutcome.id))
        ).all()
        return {
            "company_id": company_id,
            "total": len(rows),
            "outcomes": [outcome_to_dict(row) for row in rows],
        }
    finally:
        session.close()


@router.get("/{company_id}/stats")
def outcome_stats(company_id: int) -> dict[str, Any]:
    """Aggregate win/loss statistics for a company."""
    if company_id <= 0:
        raise HTTPException(status_code=400, detail="company_id must be positive")

    session = get_session()
    try:
        rows = session.scalars(
            select(TenderOutcome).where(TenderOutcome.company_id == company_id)
        ).all()
        stats = compute_win_loss_stats(list(rows))
        return {"company_id": company_id, **stats}
    finally:
        session.close()
