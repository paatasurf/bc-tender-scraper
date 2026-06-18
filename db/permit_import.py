"""Permit upsert helpers for multi-city imports."""

from __future__ import annotations

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.import_csv import BATCH_SIZE
from db.models import Permit


def upsert_city_permits(
    session: Session,
    rows: list[dict[str, str]],
    *,
    source: str,
    full_refresh: bool,
) -> int:
    rows = [row for row in rows if row.get("external_id")]
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
