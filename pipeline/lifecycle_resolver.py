"""Deterministic tender lifecycle transitions (P2-02).

No award reconciliation — date and source-presence rules only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Type

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.lifecycle_constants import (
    LIFECYCLE_AUTO_TRANSITION_SKIP_STATUSES,
    LIFECYCLE_STATUS_ACTIVE,
    LIFECYCLE_STATUS_CLOSED,
    LIFECYCLE_STATUS_CLOSING_SOON,
    LIFECYCLE_STATUS_DELISTED,
)
from db.models import ArchTender, CommercialTender, Tender
from shared.datetime_utils import normalize_dt, utc_now

logger = logging.getLogger(__name__)

CLOSING_SOON_WINDOW = timedelta(days=7)

TENDER_LIFECYCLE_MODELS: tuple[tuple[Type, str], ...] = (
    (Tender, "tenders"),
    (CommercialTender, "commercial_tenders"),
    (ArchTender, "arch_tenders"),
)


@dataclass(frozen=True)
class LifecycleRowSnapshot:
    lifecycle_status: str
    is_open: bool
    lifecycle_status_override: str | None
    closing_at: datetime | None
    closed_at: datetime | None
    missing_from_source_count: int


def _utc_now() -> datetime:
    return utc_now()


def _normalize_dt(value: datetime | None, *, now: datetime) -> datetime | None:
    return normalize_dt(value)


def has_manual_lifecycle_override(override: str | None) -> bool:
    return override is not None and str(override).strip() != ""


def evaluate_lifecycle_transition(
    row: LifecycleRowSnapshot,
    *,
    now: datetime,
) -> str | None:
    """Return the rule name applied, or None when no automatic transition."""
    if has_manual_lifecycle_override(row.lifecycle_status_override):
        return None
    if row.lifecycle_status in LIFECYCLE_AUTO_TRANSITION_SKIP_STATUSES:
        return None

    closing_at = _normalize_dt(row.closing_at, now=now)
    now = _normalize_dt(now, now=now) or now

    if closing_at is not None and closing_at <= now:
        if (
            row.lifecycle_status == LIFECYCLE_STATUS_CLOSED
            and not row.is_open
            and row.closed_at is not None
        ):
            return None
        return "closed_past_closing_at"

    if (
        closing_at is not None
        and closing_at > now
        and closing_at <= now + CLOSING_SOON_WINDOW
        and row.lifecycle_status == LIFECYCLE_STATUS_ACTIVE
    ):
        if row.lifecycle_status == LIFECYCLE_STATUS_CLOSING_SOON:
            return None
        return "closing_soon_within_7_days"

    if closing_at is None and row.missing_from_source_count >= 3:
        if row.lifecycle_status == LIFECYCLE_STATUS_DELISTED and not row.is_open:
            return None
        return "delisted_missing_from_source"

    return None


def apply_lifecycle_transition(row: Any, rule: str, *, now: datetime) -> None:
    closing_at = _normalize_dt(getattr(row, "closing_at", None), now=now)
    now = _normalize_dt(now, now=now) or now

    if rule == "closed_past_closing_at":
        row.lifecycle_status = LIFECYCLE_STATUS_CLOSED
        row.is_open = False
        if row.closed_at is None:
            row.closed_at = closing_at or now
        return

    if rule == "closing_soon_within_7_days":
        row.lifecycle_status = LIFECYCLE_STATUS_CLOSING_SOON
        row.is_open = True
        return

    if rule == "delisted_missing_from_source":
        row.lifecycle_status = LIFECYCLE_STATUS_DELISTED
        row.is_open = False
        return

    raise ValueError(f"Unknown lifecycle transition rule: {rule}")


def _empty_table_summary() -> dict[str, int]:
    return {
        "closed_past_closing_at": 0,
        "closing_soon_within_7_days": 0,
        "delisted_missing_from_source": 0,
        "skipped_override": 0,
        "skipped_no_change": 0,
    }


def _resolve_table(
    session: Session,
    model: Type[Tender] | Type[CommercialTender] | Type[ArchTender],
    *,
    now: datetime,
) -> dict[str, int]:
    summary = _empty_table_summary()
    rows = session.scalars(select(model)).all()

    for row in rows:
        if has_manual_lifecycle_override(row.lifecycle_status_override):
            summary["skipped_override"] += 1
            continue

        snapshot = LifecycleRowSnapshot(
            lifecycle_status=row.lifecycle_status,
            is_open=row.is_open,
            lifecycle_status_override=row.lifecycle_status_override,
            closing_at=row.closing_at,
            closed_at=row.closed_at,
            missing_from_source_count=row.missing_from_source_count,
        )
        rule = evaluate_lifecycle_transition(snapshot, now=now)
        if rule is None:
            summary["skipped_no_change"] += 1
            continue

        apply_lifecycle_transition(row, rule, now=now)
        summary[rule] += 1

    return summary


def resolve_tender_lifecycle(
    session: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Apply P2-02 lifecycle rules to all tender tables. Idempotent."""
    resolved_at = now or _utc_now()
    tables: dict[str, dict[str, int]] = {}
    totals = _empty_table_summary()

    for model, table_name in TENDER_LIFECYCLE_MODELS:
        table_summary = _resolve_table(session, model, now=resolved_at)
        tables[table_name] = table_summary
        for key, count in table_summary.items():
            totals[key] += count

    if commit:
        session.commit()

    payload = {
        "resolved_at": resolved_at.isoformat(),
        "tables": tables,
        "totals": totals,
    }
    logger.info("[Lifecycle] resolve summary: %s", payload)
    return payload
