"""Presence-aware upsert for federal, commercial, and architecture tenders (P1-02)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence, Type

from sqlalchemy import case, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.constants import BATCH_SIZE, COMMERCIAL_BATCH_SIZE
from db.models import ArchTender, CommercialTender, Tender

TENDER_CONTENT_COLUMNS: tuple[str, ...] = (
    "title",
    "organization",
    "category",
    "posted_date",
    "closing_date",
    "estimated_value",
    "location",
    "tender_id",
    "source",
)

COMMERCIAL_CONTENT_COLUMNS: tuple[str, ...] = (
    "title",
    "company",
    "value",
    "deadline",
    "status",
    "category",
    "tender_id",
    "source",
)

ARCH_CONTENT_COLUMNS: tuple[str, ...] = (
    "title",
    "company",
    "value",
    "deadline",
    "status",
    "category",
    "tender_id",
)

PRESENCE_SKIP_ON_UPDATE = frozenset({"id", "scraped_at", "first_seen_at"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp_presence_for_insert(row: dict[str, Any], *, seen_at: datetime) -> dict[str, Any]:
    stamped = dict(row)
    stamped["first_seen_at"] = seen_at
    stamped["last_seen_at"] = seen_at
    stamped["updated_at"] = seen_at
    return stamped


def _content_changed_predicate(table, excluded, content_columns: Sequence[str]):
    predicates = [
        table.c[column].is_distinct_from(excluded[column])
        for column in content_columns
        if column in table.c
    ]
    if not predicates:
        return False
    return or_(*predicates)


def upsert_with_presence(
    session: Session,
    model: Type[Tender] | Type[CommercialTender] | Type[ArchTender],
    rows: list[dict[str, Any]],
    conflict_column: str,
    content_columns: Sequence[str],
    *,
    preserve_on_update: frozenset[str] = frozenset(),
    batch_size: int = BATCH_SIZE,
) -> int:
    """Upsert tender rows with first_seen_at / last_seen_at / updated_at semantics."""
    if not rows:
        return 0

    table = model.__table__
    seen_at = _utc_now()
    imported = 0
    skip_on_update = PRESENCE_SKIP_ON_UPDATE | preserve_on_update

    for start in range(0, len(rows), batch_size):
        batch = [_stamp_presence_for_insert(row, seen_at=seen_at) for row in rows[start : start + batch_size]]
        stmt = insert(table).values(batch)
        excluded = stmt.excluded
        content_changed = _content_changed_predicate(table, excluded, content_columns)

        update_cols = {
            col.name: excluded[col.name]
            for col in table.columns
            if col.name not in skip_on_update
        }
        update_cols["first_seen_at"] = table.c.first_seen_at
        update_cols["last_seen_at"] = func.now()
        update_cols["updated_at"] = case(
            (content_changed, func.now()),
            else_=table.c.updated_at,
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=[conflict_column],
            set_=update_cols,
        )
        session.execute(stmt)
        session.commit()
        imported += len(batch)

    return imported
