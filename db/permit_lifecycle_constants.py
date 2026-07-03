"""Canonical permit lifecycle vocabulary (schema + resolver)."""

from __future__ import annotations

from typing import Any

PERMIT_LIFECYCLE_STATUS_ACTIVE = "active"
PERMIT_LIFECYCLE_STATUS_COMPLETED = "completed"
PERMIT_LIFECYCLE_STATUS_CANCELLED = "cancelled"
PERMIT_LIFECYCLE_STATUS_STALE = "stale"
PERMIT_LIFECYCLE_STATUS_UNKNOWN = "unknown"

PERMIT_LIFECYCLE_STATUSES: tuple[str, ...] = (
    PERMIT_LIFECYCLE_STATUS_ACTIVE,
    PERMIT_LIFECYCLE_STATUS_COMPLETED,
    PERMIT_LIFECYCLE_STATUS_CANCELLED,
    PERMIT_LIFECYCLE_STATUS_STALE,
    PERMIT_LIFECYCLE_STATUS_UNKNOWN,
)

PERMIT_STALE_AGE_DAYS = 730  # 24 months — conservative vs ~9–11mo median completion

# Vancouver / municipal source vocabulary (case-insensitive match on normalized raw status).
PERMIT_SOURCE_STATUS_COMPLETED: frozenset[str] = frozenset(
    {
        "finaled",
        "finalled",
        "completed",
        "complete",
        "closed",
    }
)
PERMIT_SOURCE_STATUS_CANCELLED: frozenset[str] = frozenset(
    {
        "cancelled",
        "canceled",
        "withdrawn",
    }
)
PERMIT_SOURCE_STATUS_ACTIVE: frozenset[str] = frozenset(
    {
        "issued",
        "open",
        "in review",
        "in_review",
    }
)

# Lifecycle + source-status columns managed by resolver/backfill — never overwritten by scraper upserts.
PERMIT_LIFECYCLE_IMPORT_SKIP_COLUMNS: frozenset[str] = frozenset(
    {
        "lifecycle_status",
        "lifecycle_status_override",
        "status_changed_at",
        "is_active",
        "source_status_raw",
    }
)


def permit_lifecycle_eligible(row: Any, *, include_inactive: bool = False) -> bool:
    """Return True when a permit row is eligible for open-market consumer pipelines."""
    if include_inactive:
        return True
    return bool(getattr(row, "is_active", True))


def apply_active_permit_filter(stmt: Any, *, include_inactive: bool = False) -> Any:
    """SQLAlchemy helper: default open-market filter on permits.is_active."""
    if include_inactive:
        return stmt
    from db.models import Permit

    return stmt.where(Permit.is_active.is_(True))
