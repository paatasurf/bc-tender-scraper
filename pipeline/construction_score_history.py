"""Helpers for construction score history (schema prepared; pipeline not wired yet)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CompanyScoreHistory
from pipeline.construction_tier_config import CONSTRUCTION_TIER_VERSION


def build_score_history_row(
    *,
    company_id: int,
    construction_score: int,
    company_tier: str,
    calculated_at: datetime | None = None,
    algorithm_version: int | None = None,
) -> CompanyScoreHistory:
    """Create an unsaved score history row."""
    return CompanyScoreHistory(
        company_id=company_id,
        construction_score=construction_score,
        company_tier=company_tier,
        calculated_at=calculated_at or datetime.now(timezone.utc),
        algorithm_version=algorithm_version if algorithm_version is not None else CONSTRUCTION_TIER_VERSION,
    )


def record_score_snapshot(
    session: Session,
    *,
    company_id: int,
    construction_score: int,
    company_tier: str,
    calculated_at: datetime | None = None,
    algorithm_version: int | None = None,
    commit: bool = False,
) -> CompanyScoreHistory:
    """Persist one score snapshot. Not called by the tier pipeline yet."""
    row = build_score_history_row(
        company_id=company_id,
        construction_score=construction_score,
        company_tier=company_tier,
        calculated_at=calculated_at,
        algorithm_version=algorithm_version,
    )
    session.add(row)
    if commit:
        session.commit()
    return row


def get_score_history(
    session: Session,
    company_id: int,
    *,
    limit: int = 50,
) -> list[CompanyScoreHistory]:
    """Return recent score snapshots for a company (newest first)."""
    return list(
        session.scalars(
            select(CompanyScoreHistory)
            .where(CompanyScoreHistory.company_id == company_id)
            .order_by(CompanyScoreHistory.calculated_at.desc())
            .limit(limit)
        ).all()
    )


def score_history_to_dict(row: CompanyScoreHistory) -> dict[str, Any]:
    return {
        "company_id": row.company_id,
        "construction_score": row.construction_score,
        "company_tier": row.company_tier,
        "calculated_at": row.calculated_at.isoformat() if row.calculated_at else None,
        "algorithm_version": row.algorithm_version,
    }
