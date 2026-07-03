"""Canonical company lifecycle vocabulary (schema + resolver)."""

from __future__ import annotations

COMPANY_LIFECYCLE_STATUS_ACTIVE = "active"
COMPANY_LIFECYCLE_STATUS_QUIET = "quiet"
COMPANY_LIFECYCLE_STATUS_DORMANT = "dormant"
COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE = "no_observable_activity"

COMPANY_LIFECYCLE_STATUSES: tuple[str, ...] = (
    COMPANY_LIFECYCLE_STATUS_ACTIVE,
    COMPANY_LIFECYCLE_STATUS_QUIET,
    COMPANY_LIFECYCLE_STATUS_DORMANT,
    COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE,
)

COMPANY_ACTIVE_AGE_DAYS = 365  # <12 months
COMPANY_QUIET_AGE_DAYS = 730  # 12–24 months; >730 dormant

# Resolver-managed columns — never overwritten by company stats upserts.
COMPANY_LIFECYCLE_IMPORT_SKIP_COLUMNS: frozenset[str] = frozenset(
    {
        "lifecycle_status",
        "lifecycle_status_override",
        "last_activity_at",
        "status_changed_at",
        "is_operating",
    }
)


def is_operating_for_status(status: str) -> bool:
    return status in {
        COMPANY_LIFECYCLE_STATUS_ACTIVE,
        COMPANY_LIFECYCLE_STATUS_QUIET,
        COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE,
    }


def lifecycle_status_from_age_days(age_days: int | None) -> str:
    if age_days is None:
        return COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE
    if age_days <= COMPANY_ACTIVE_AGE_DAYS:
        return COMPANY_LIFECYCLE_STATUS_ACTIVE
    if age_days <= COMPANY_QUIET_AGE_DAYS:
        return COMPANY_LIFECYCLE_STATUS_QUIET
    return COMPANY_LIFECYCLE_STATUS_DORMANT
