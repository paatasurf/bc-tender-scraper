"""Deterministic company lifecycle transitions (Company Lifecycle Phase 2).

Activity dates come from verified FK joins only — no text-name matching.
Sources: contract_awards.company_id + award_date, tender_outcomes.recorded_at.
Permits are included when permits.company_id FK exists and is populated (future phase).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.company_lifecycle_constants import (
    COMPANY_LIFECYCLE_STATUS_ACTIVE,
    COMPANY_LIFECYCLE_STATUS_DORMANT,
    COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE,
    COMPANY_LIFECYCLE_STATUS_QUIET,
    is_operating_for_status,
    lifecycle_status_from_age_days,
)
from db.models import Company, ContractAward, TenderOutcome
from pipeline.lifecycle_resolver import has_manual_lifecycle_override
from shared.datetime_utils import normalize_dt, parse_iso_date, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompanyLifecycleSnapshot:
    lifecycle_status: str
    is_operating: bool
    lifecycle_status_override: str | None
    last_activity_at: datetime | None


def _utc_now() -> datetime:
    return utc_now()


def _normalize_dt(value: datetime | None) -> datetime | None:
    return normalize_dt(value)


def _parse_iso_date(raw: str | None) -> date | None:
    return parse_iso_date(raw)


def iso_date_to_activity_timestamp(raw: str | None) -> datetime | None:
    parsed = _parse_iso_date(raw)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, tzinfo=timezone.utc)


def age_days_since_activity(last_activity: datetime, *, now: datetime) -> int:
    last_date = _normalize_dt(last_activity).date()
    now_date = _normalize_dt(now).date()
    return (now_date - last_date).days


def expected_lifecycle_for_activity(
    last_activity: datetime | None,
    *,
    now: datetime,
) -> tuple[str, bool]:
    if last_activity is None:
        status = COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE
    else:
        age = age_days_since_activity(last_activity, now=now)
        status = lifecycle_status_from_age_days(age)
    return status, is_operating_for_status(status)


def evaluate_company_lifecycle_transition(
    row: CompanyLifecycleSnapshot,
    *,
    computed_last_activity: datetime | None,
    now: datetime,
) -> str | None:
    """Return rule name applied, or None when no automatic transition."""
    if has_manual_lifecycle_override(row.lifecycle_status_override):
        return None

    expected_status, expected_operating = expected_lifecycle_for_activity(
        computed_last_activity,
        now=now,
    )
    normalized_computed = _normalize_dt(computed_last_activity)
    normalized_stored = _normalize_dt(row.last_activity_at)

    if (
        row.lifecycle_status == expected_status
        and row.is_operating == expected_operating
        and normalized_stored == normalized_computed
    ):
        return None

    if expected_status == COMPANY_LIFECYCLE_STATUS_ACTIVE:
        return "status_active"
    if expected_status == COMPANY_LIFECYCLE_STATUS_QUIET:
        return "status_quiet"
    if expected_status == COMPANY_LIFECYCLE_STATUS_DORMANT:
        return "status_dormant"
    return "status_no_observable_activity"


def apply_company_lifecycle_transition(
    row: Any,
    rule: str,
    *,
    computed_last_activity: datetime | None,
    now: datetime,
) -> None:
    expected_status, expected_operating = expected_lifecycle_for_activity(
        computed_last_activity,
        now=now,
    )
    if rule == "status_active":
        row.lifecycle_status = COMPANY_LIFECYCLE_STATUS_ACTIVE
    elif rule == "status_quiet":
        row.lifecycle_status = COMPANY_LIFECYCLE_STATUS_QUIET
    elif rule == "status_dormant":
        row.lifecycle_status = COMPANY_LIFECYCLE_STATUS_DORMANT
    elif rule == "status_no_observable_activity":
        row.lifecycle_status = COMPANY_LIFECYCLE_STATUS_NO_OBSERVABLE
    else:
        raise ValueError(f"Unknown company lifecycle rule: {rule}")

    row.is_operating = expected_operating
    row.last_activity_at = _normalize_dt(computed_last_activity)
    row.status_changed_at = now


def _empty_summary() -> dict[str, int]:
    return {
        "status_active": 0,
        "status_quiet": 0,
        "status_dormant": 0,
        "status_no_observable_activity": 0,
        "skipped_override": 0,
        "skipped_no_change": 0,
    }


def _load_award_activity(session: Session) -> dict[int, datetime]:
    rows = session.execute(
        select(
            ContractAward.company_id,
            func.max(ContractAward.award_date),
        )
        .where(ContractAward.company_id.is_not(None))
        .where(ContractAward.award_date != "")
        .group_by(ContractAward.company_id)
    ).all()
    result: dict[int, datetime] = {}
    for company_id, max_date in rows:
        if company_id is None:
            continue
        ts = iso_date_to_activity_timestamp(max_date)
        if ts is not None:
            result[int(company_id)] = ts
    return result


def _load_outcome_activity(session: Session) -> dict[int, datetime]:
    rows = session.execute(
        select(
            TenderOutcome.company_id,
            func.max(TenderOutcome.recorded_at),
        ).group_by(TenderOutcome.company_id)
    ).all()
    result: dict[int, datetime] = {}
    for company_id, max_recorded in rows:
        normalized = _normalize_dt(max_recorded)
        if normalized is not None:
            result[int(company_id)] = normalized
    return result


def _load_permit_activity(session: Session) -> dict[int, datetime]:
    """Load permit activity when permits.company_id FK exists; otherwise empty."""
    permit_table = session.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'permits'
              AND column_name = 'company_id'
            """
        )
    ).first()
    if permit_table is None:
        return {}

    rows = session.execute(
        text(
            """
            SELECT company_id,
                   MAX(GREATEST(
                       NULLIF(issue_date, ''),
                       NULLIF(application_date, '')
                   )) AS max_date
            FROM permits
            WHERE company_id IS NOT NULL
              AND (issue_date <> '' OR application_date <> '')
            GROUP BY company_id
            """
        )
    ).all()
    result: dict[int, datetime] = {}
    for company_id, max_date in rows:
        if company_id is None or not max_date:
            continue
        ts = iso_date_to_activity_timestamp(str(max_date))
        if ts is not None:
            result[int(company_id)] = ts
    return result


def _merge_activity_maps(*maps: dict[int, datetime]) -> dict[int, datetime]:
    merged: dict[int, datetime] = {}
    for activity_map in maps:
        for company_id, ts in activity_map.items():
            existing = merged.get(company_id)
            if existing is None or ts > existing:
                merged[company_id] = ts
    return merged


def _compute_last_activity_for_company(
    company_id: int,
    activity_by_company: dict[int, datetime],
) -> datetime | None:
    return activity_by_company.get(company_id)


def resolve_company_lifecycle(
    session: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Apply company lifecycle rules. Idempotent."""
    resolved_at = now or _utc_now()
    award_activity = _load_award_activity(session)
    outcome_activity = _load_outcome_activity(session)
    permit_activity = _load_permit_activity(session)
    activity_by_company = _merge_activity_maps(
        award_activity,
        outcome_activity,
        permit_activity,
    )

    summary = _empty_summary()
    companies = session.scalars(select(Company).order_by(Company.id)).all()

    for row in companies:
        if has_manual_lifecycle_override(row.lifecycle_status_override):
            summary["skipped_override"] += 1
            continue

        computed_last = _compute_last_activity_for_company(row.id, activity_by_company)
        snapshot = CompanyLifecycleSnapshot(
            lifecycle_status=row.lifecycle_status,
            is_operating=row.is_operating,
            lifecycle_status_override=row.lifecycle_status_override,
            last_activity_at=row.last_activity_at,
        )
        rule = evaluate_company_lifecycle_transition(
            snapshot,
            computed_last_activity=computed_last,
            now=resolved_at,
        )
        if rule is None:
            summary["skipped_no_change"] += 1
            continue

        apply_company_lifecycle_transition(
            row,
            rule,
            computed_last_activity=computed_last,
            now=resolved_at,
        )
        summary[rule] += 1

    if commit:
        session.commit()

    payload = {
        "resolved_at": resolved_at.isoformat(),
        "activity_sources": {
            "award_linked_companies": len(award_activity),
            "outcome_linked_companies": len(outcome_activity),
            "permit_linked_companies": len(permit_activity),
        },
        "totals": summary,
    }
    logger.info("[CompanyLifecycle] resolve summary: %s", payload)
    return payload


def run_company_lifecycle_resolve_job() -> dict[str, Any]:
    """Entrypoint for sync HTTP or FastAPI background task — owns session lifecycle."""
    from db.connection import get_session, init_db

    init_db()
    session = get_session()
    try:
        return resolve_company_lifecycle(session)
    except Exception:
        logger.exception("[CompanyLifecycle] resolve job failed")
        raise
    finally:
        session.close()
