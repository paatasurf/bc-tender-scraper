"""Deterministic permit lifecycle transitions (Permit Lifecycle Phase 2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Permit
from db.permit_lifecycle_constants import (
    PERMIT_LIFECYCLE_STATUS_ACTIVE,
    PERMIT_LIFECYCLE_STATUS_CANCELLED,
    PERMIT_LIFECYCLE_STATUS_COMPLETED,
    PERMIT_LIFECYCLE_STATUS_STALE,
    PERMIT_LIFECYCLE_STATUS_UNKNOWN,
    PERMIT_SOURCE_STATUS_ACTIVE,
    PERMIT_SOURCE_STATUS_CANCELLED,
    PERMIT_SOURCE_STATUS_COMPLETED,
    PERMIT_STALE_AGE_DAYS,
)
from pipeline.lifecycle_resolver import has_manual_lifecycle_override

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PermitLifecycleSnapshot:
    lifecycle_status: str
    is_active: bool
    lifecycle_status_override: str | None
    source_status_raw: str
    issue_date: str
    application_date: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    text = str(raw).strip().replace("/", "-")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def permit_reference_date(*, issue_date: str | None, application_date: str | None) -> date | None:
    candidates = [_parse_iso_date(issue_date), _parse_iso_date(application_date)]
    parsed = [value for value in candidates if value is not None]
    return max(parsed) if parsed else None


def normalize_source_status(raw: str | None) -> str:
    return (raw or "").strip().lower()


def lifecycle_from_source_status(raw: str | None) -> str | None:
    normalized = normalize_source_status(raw)
    if not normalized:
        return None
    if normalized in PERMIT_SOURCE_STATUS_COMPLETED:
        return PERMIT_LIFECYCLE_STATUS_COMPLETED
    if normalized in PERMIT_SOURCE_STATUS_CANCELLED:
        return PERMIT_LIFECYCLE_STATUS_CANCELLED
    if normalized in PERMIT_SOURCE_STATUS_ACTIVE:
        return PERMIT_LIFECYCLE_STATUS_ACTIVE
    return PERMIT_LIFECYCLE_STATUS_UNKNOWN


def is_active_for_status(status: str) -> bool:
    return status in {PERMIT_LIFECYCLE_STATUS_ACTIVE, PERMIT_LIFECYCLE_STATUS_UNKNOWN}


def evaluate_permit_lifecycle_transition(
    row: PermitLifecycleSnapshot,
    *,
    now: datetime,
) -> str | None:
    """Return rule name applied, or None when no automatic transition."""
    if has_manual_lifecycle_override(row.lifecycle_status_override):
        return None

    now_date = now.astimezone(timezone.utc).date()
    source_status = lifecycle_from_source_status(row.source_status_raw)
    reference = permit_reference_date(
        issue_date=row.issue_date,
        application_date=row.application_date,
    )

    if row.source_status_raw.strip():
        if source_status == PERMIT_LIFECYCLE_STATUS_COMPLETED:
            if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_COMPLETED and not row.is_active:
                return None
            return "source_status_completed"
        if source_status == PERMIT_LIFECYCLE_STATUS_CANCELLED:
            if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_CANCELLED and not row.is_active:
                return None
            return "source_status_cancelled"
        if source_status == PERMIT_LIFECYCLE_STATUS_ACTIVE:
            if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_ACTIVE and row.is_active:
                return None
            return "source_status_active"
        if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_UNKNOWN and row.is_active:
            return None
        return "source_status_unknown"

    if reference is None:
        if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_UNKNOWN and row.is_active:
            return None
        return "no_status_no_dates_unknown"

    stale_cutoff = now_date - timedelta(days=PERMIT_STALE_AGE_DAYS)
    if reference < stale_cutoff:
        if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_STALE and not row.is_active:
            return None
        return "age_stale_24mo"

    if row.lifecycle_status == PERMIT_LIFECYCLE_STATUS_ACTIVE and row.is_active:
        return None
    return "age_active"


def apply_permit_lifecycle_transition(row: Any, rule: str, *, now: datetime) -> None:
    if rule == "source_status_completed":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_COMPLETED
        row.is_active = False
    elif rule == "source_status_cancelled":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_CANCELLED
        row.is_active = False
    elif rule == "source_status_active":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_ACTIVE
        row.is_active = True
    elif rule == "source_status_unknown":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_UNKNOWN
        row.is_active = True
    elif rule == "no_status_no_dates_unknown":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_UNKNOWN
        row.is_active = True
    elif rule == "age_stale_24mo":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_STALE
        row.is_active = False
    elif rule == "age_active":
        row.lifecycle_status = PERMIT_LIFECYCLE_STATUS_ACTIVE
        row.is_active = True
    else:
        raise ValueError(f"Unknown permit lifecycle rule: {rule}")

    row.status_changed_at = now


def _empty_summary() -> dict[str, int]:
    return {
        "source_status_completed": 0,
        "source_status_cancelled": 0,
        "source_status_active": 0,
        "source_status_unknown": 0,
        "no_status_no_dates_unknown": 0,
        "age_stale_24mo": 0,
        "age_active": 0,
        "skipped_override": 0,
        "skipped_no_change": 0,
    }


def _resolve_city(
    session: Session,
    *,
    source: str,
    now: datetime,
) -> dict[str, int]:
    summary = _empty_summary()
    rows = session.scalars(select(Permit).where(Permit.source == source)).all()

    for row in rows:
        if has_manual_lifecycle_override(row.lifecycle_status_override):
            summary["skipped_override"] += 1
            continue

        snapshot = PermitLifecycleSnapshot(
            lifecycle_status=row.lifecycle_status,
            is_active=row.is_active,
            lifecycle_status_override=row.lifecycle_status_override,
            source_status_raw=row.source_status_raw or "",
            issue_date=row.issue_date or "",
            application_date=row.application_date or "",
        )
        rule = evaluate_permit_lifecycle_transition(snapshot, now=now)
        if rule is None:
            summary["skipped_no_change"] += 1
            continue

        apply_permit_lifecycle_transition(row, rule, now=now)
        summary[rule] += 1

    return summary


def resolve_permit_lifecycle(
    session: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Apply permit lifecycle rules. Idempotent."""
    resolved_at = now or _utc_now()
    sources = [
        row[0]
        for row in session.execute(
            select(Permit.source).distinct().order_by(Permit.source)
        ).all()
    ]

    cities: dict[str, dict[str, int]] = {}
    totals = _empty_summary()

    for source in sources:
        city_summary = _resolve_city(session, source=source, now=resolved_at)
        cities[source] = city_summary
        for key, count in city_summary.items():
            totals[key] += count

    if commit:
        session.commit()

    payload = {
        "resolved_at": resolved_at.isoformat(),
        "stale_age_days": PERMIT_STALE_AGE_DAYS,
        "cities": cities,
        "totals": totals,
    }
    logger.info("[PermitLifecycle] resolve summary: %s", payload)
    return payload
