from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models import ArchTender, Job, Permit, RedditSignal, Tender
from scraper.config import (
    ARCH_TENDERS_CSV,
    BUILDING_PERMITS_CSV,
    JOB_BANK_JOBS_CSV,
    OUTPUT_CSV,
    REDDIT_SIGNALS_CSV,
)

BATCH_SIZE = 500


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        print(f"[Import] Skipping missing file: {path}")
        return []

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _upsert_batch(session: Session, model, rows: list[dict], conflict_column: str) -> int:
    if not rows:
        return 0

    table = model.__table__
    imported = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        stmt = insert(table).values(batch)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in {"id", "scraped_at"}
        }
        stmt = stmt.on_conflict_do_update(index_elements=[conflict_column], set_=update_cols)
        session.execute(stmt)
        imported += len(batch)
    session.commit()
    return imported


def import_tenders(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(OUTPUT_CSV))
    payload = [
        {
            "title": row.get("title", ""),
            "organization": row.get("organization", ""),
            "category": row.get("category", ""),
            "posted_date": row.get("posted_date", ""),
            "closing_date": row.get("closing_date", ""),
            "estimated_value": row.get("estimated_value", ""),
            "location": row.get("location", ""),
            "tender_id": row.get("tender_id", ""),
            "url": row.get("url", ""),
            "source": row.get("source", ""),
        }
        for row in rows
        if row.get("url")
    ]
    count = _upsert_batch(session, Tender, payload, "url")
    print(f"[Import] Tenders: {count} rows")
    return count


def import_permits(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(BUILDING_PERMITS_CSV))
    payload = [
        {
            "address": row.get("address", ""),
            "permit_type": row.get("permit_type", ""),
            "project_value": row.get("project_value", ""),
            "applicant": row.get("applicant", ""),
            "issue_date": row.get("issue_date", ""),
            "description": row.get("description", ""),
        }
        for row in rows
        if row.get("address")
    ]

    if not payload:
        return 0

    session.execute(Permit.__table__.delete())
    session.commit()

    imported = 0
    for start in range(0, len(payload), BATCH_SIZE):
        batch = payload[start : start + BATCH_SIZE]
        session.execute(insert(Permit.__table__), batch)
        session.commit()
        imported += len(batch)

    print(f"[Import] Permits: {imported} rows (full refresh)")
    return imported


def import_reddit(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(REDDIT_SIGNALS_CSV))
    payload = []
    for row in rows:
        if not row.get("url"):
            continue
        try:
            upvotes = int(row.get("upvotes") or 0)
        except ValueError:
            upvotes = 0
        payload.append(
            {
                "title": row.get("title", ""),
                "text": row.get("text", ""),
                "upvotes": upvotes,
                "date": row.get("date", ""),
                "url": row.get("url", ""),
            }
        )
    count = _upsert_batch(session, RedditSignal, payload, "url")
    print(f"[Import] Reddit: {count} rows")
    return count


def import_jobs(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(JOB_BANK_JOBS_CSV))
    payload = [
        {
            "job_title": row.get("job_title", ""),
            "company": row.get("company", ""),
            "location": row.get("location", ""),
            "salary": row.get("salary", ""),
            "date": row.get("date", ""),
            "url": row.get("url", ""),
        }
        for row in rows
        if row.get("url")
    ]
    count = _upsert_batch(session, Job, payload, "url")
    print(f"[Import] Jobs: {count} rows")
    return count


def import_arch_tenders(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(ARCH_TENDERS_CSV))
    payload = [
        {
            "title": row.get("title", ""),
            "company": row.get("company", ""),
            "value": row.get("value", ""),
            "deadline": row.get("deadline", ""),
            "status": row.get("status", ""),
            "category": row.get("category", ""),
            "url": row.get("url", ""),
            "tender_id": row.get("tender_id", ""),
        }
        for row in rows
        if row.get("url")
    ]
    count = _upsert_batch(session, ArchTender, payload, "url")
    print(f"[Import] Architecture tenders: {count} rows")
    return count


def import_all_csvs(session: Session) -> dict[str, int]:
    print("[Import] Starting CSV import into PostgreSQL")
    return {
        "tenders": import_tenders(session),
        "permits": import_permits(session),
        "reddit": import_reddit(session),
        "jobs": import_jobs(session),
        "arch_tenders": import_arch_tenders(session),
    }
