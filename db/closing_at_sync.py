"""Sync closing_at from raw deadline columns (P2-06)."""

from __future__ import annotations

from typing import Sequence, Type

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.closing_at_parser import parse_closing_at
from db.models import ArchTender, CommercialTender, Tender

TENDER_DEADLINE_SPECS: tuple[tuple[Type, str, str], ...] = (
    (Tender, "closing_date", "tenders"),
    (CommercialTender, "deadline", "commercial_tenders"),
    (ArchTender, "deadline", "arch_tenders"),
)


def sync_closing_at_from_deadline(
    session: Session,
    model: Type[Tender] | Type[CommercialTender] | Type[ArchTender],
    deadline_field: str,
    *,
    urls: set[str] | None = None,
    only_null: bool = False,
) -> dict[str, int]:
    """Populate closing_at from the table's string deadline field when parseable."""
    query = select(model)
    if urls:
        query = query.where(model.url.in_(urls))
    if only_null:
        query = query.where(model.closing_at.is_(None))

    updated = 0
    skipped_unparseable = 0
    for row in session.scalars(query):
        parsed = parse_closing_at(getattr(row, deadline_field))
        if parsed is None:
            skipped_unparseable += 1
            continue
        if row.closing_at == parsed:
            continue
        row.closing_at = parsed
        updated += 1

    if updated:
        session.commit()

    return {
        "updated": updated,
        "skipped_unparseable": skipped_unparseable,
    }


def count_closing_at_state(session: Session, model: Type) -> dict[str, int]:
    total = session.scalar(select(func.count()).select_from(model)) or 0
    populated = session.scalar(select(func.count()).select_from(model).where(model.closing_at.is_not(None))) or 0
    return {
        "total": total,
        "closing_at_set": populated,
        "closing_at_null": total - populated,
    }


def backfill_all_tender_closing_at(
    session: Session,
    *,
    only_null: bool = True,
) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for model, deadline_field, table_name in TENDER_DEADLINE_SPECS:
        before = count_closing_at_state(session, model)
        sync_result = sync_closing_at_from_deadline(
            session,
            model,
            deadline_field,
            only_null=only_null,
        )
        after = count_closing_at_state(session, model)
        summary[table_name] = {
            **sync_result,
            "before_set": before["closing_at_set"],
            "before_null": before["closing_at_null"],
            "after_set": after["closing_at_set"],
            "after_null": after["closing_at_null"],
        }
    return summary
