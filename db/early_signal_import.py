"""Upsert helpers for early_signal_events."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.constants import BATCH_SIZE
from db.models import EarlySignalEvent


def _dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        external_id = row.get("external_id") or ""
        if not external_id:
            continue
        deduped[(row.get("source") or "", external_id)] = row
    return list(deduped.values())


def upsert_early_signal_events(session: Session, rows: list[dict[str, str]]) -> int:
    rows = _dedupe_rows(rows)
    if not rows:
        return 0

    table = EarlySignalEvent.__table__
    imported = 0
    # scraped_at is deliberately NOT in this set: it must update on every
    # re-upsert to reflect the actual last-scrape time, not just the
    # first-ever INSERT. Its server_default=func.now() (db/models.py)
    # means the column isn't in `batch`'s row dicts at all -- PostgreSQL
    # still computes EXCLUDED.scraped_at as the default-derived value for
    # this attempt (a fresh now()), so listing it in update_cols below is
    # sufficient; no explicit value needs to be added to `batch`.
    skip_on_update = {"id", "address", "applicant", "project_value"}

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
