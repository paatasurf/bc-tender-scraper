"""Permit upsert helpers for multi-city imports."""

from __future__ import annotations

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.import_csv import BATCH_SIZE
from db.models import Permit


def _dedupe_permit_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        external_id = row.get("external_id") or ""
        if not external_id:
            continue
        deduped[(row.get("source") or "", external_id)] = row
    return list(deduped.values())


def upsert_city_permits(
    session: Session,
    rows: list[dict[str, str]],
    *,
    source: str,
    full_refresh: bool,
) -> int:
    rows = _dedupe_permit_rows(rows)
    if not rows:
        return 0

    if full_refresh:
        session.execute(delete(Permit).where(Permit.source == source))
        session.commit()

    table = Permit.__table__
    imported = 0
    skip_on_update = {"id", "scraped_at"}

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        stmt = insert(table).values(batch)
        if full_refresh:
            session.execute(stmt)
        else:
            update_cols = {
                col.name: stmt.excluded[col.name]
                for col in table.columns
                if col.name not in skip_on_update
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "external_id"],
                index_where=text("external_id <> ''"),
                set_=update_cols,
            )
            session.execute(stmt)
        session.commit()
        imported += len(batch)

    return imported
