"""Canonical tender lifecycle vocabulary (schema only — no resolver logic)."""

from __future__ import annotations

# Approved lifecycle states (Architecture v2)
LIFECYCLE_STATUS_NEW = "new"
LIFECYCLE_STATUS_ACTIVE = "active"
LIFECYCLE_STATUS_CLOSING_SOON = "closing_soon"
LIFECYCLE_STATUS_CLOSED = "closed"
LIFECYCLE_STATUS_AWARDED = "awarded"
LIFECYCLE_STATUS_CANCELLED = "cancelled"
LIFECYCLE_STATUS_OUTCOME_UNKNOWN = "outcome_unknown"
LIFECYCLE_STATUS_ARCHIVED = "archived"
LIFECYCLE_STATUS_DELISTED = "delisted"

LIFECYCLE_STATUSES: tuple[str, ...] = (
    LIFECYCLE_STATUS_NEW,
    LIFECYCLE_STATUS_ACTIVE,
    LIFECYCLE_STATUS_CLOSING_SOON,
    LIFECYCLE_STATUS_CLOSED,
    LIFECYCLE_STATUS_AWARDED,
    LIFECYCLE_STATUS_CANCELLED,
    LIFECYCLE_STATUS_OUTCOME_UNKNOWN,
    LIFECYCLE_STATUS_ARCHIVED,
    LIFECYCLE_STATUS_DELISTED,
)

# Reconciliation / manual states — automatic P2-02 rules do not override these.
LIFECYCLE_AUTO_TRANSITION_SKIP_STATUSES: frozenset[str] = frozenset(
    {
        LIFECYCLE_STATUS_AWARDED,
        LIFECYCLE_STATUS_CANCELLED,
        LIFECYCLE_STATUS_ARCHIVED,
        LIFECYCLE_STATUS_OUTCOME_UNKNOWN,
    }
)

# Columns managed by lifecycle engine / manual override — never overwritten by CSV import.
LIFECYCLE_IMPORT_SKIP_COLUMNS: frozenset[str] = frozenset(
    {
        "lifecycle_status",
        "is_open",
        "lifecycle_status_override",
        "lifecycle_override_reason",
        "lifecycle_override_by",
        "closing_at",
        "closed_at",
        "awarded_at",
        "cancelled_at",
        "archived_at",
        "missing_from_source_count",
        "source_status_raw",
        "source_status_normalized",
        "award_id",
        "award_match_confidence",
        "addenda_count",
        "last_addendum_at",
    }
)
