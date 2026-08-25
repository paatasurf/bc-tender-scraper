from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.constants import BATCH_SIZE, COMMERCIAL_BATCH_SIZE
from db.closing_at_sync import sync_closing_at_from_deadline
from db.models import (
    ArchTender,
    CommercialTender,
    Job,
    LinkedInSignal,
    NewsSignal,
    RedditSignal,
    Tender,
)
from db.permit_import import upsert_city_permits
from db.tender_presence import (
    ARCH_CONTENT_COLUMNS,
    COMMERCIAL_CONTENT_COLUMNS,
    TENDER_CONTENT_COLUMNS,
    sync_missing_from_source_counts,
    upsert_with_presence,
)
from scraper.tender_category import resolve_tender_category
from scraper.config import (
    ARCH_TENDERS_CSV,
    BUILDING_PERMITS_CSV,
    COMMERCIAL_TENDERS_CSV,
    JOB_BANK_JOBS_CSV,
    LINKEDIN_SIGNALS_CSV,
    NEWS_SIGNALS_CSV,
    OUTPUT_CSV,
    REDDIT_SIGNALS_CSV,
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        print(f"[Import] Skipping missing file: {path}")
        return []

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


AI_PRESERVE_COLUMNS = frozenset({"ai_score", "ai_summary", "ai_budget_estimate"})


def _upsert_batch(
    session: Session,
    model,
    rows: list[dict],
    conflict_column: str,
    preserve_on_update: frozenset[str] = frozenset(),
    *,
    batch_size: int = BATCH_SIZE,
) -> int:
    if not rows:
        return 0

    table = model.__table__
    imported = 0
    skip_on_update = {"id", "scraped_at"} | preserve_on_update
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        stmt = insert(table).values(batch)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in table.columns
            if col.name not in skip_on_update
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[conflict_column], set_=update_cols
        )
        session.execute(stmt)
        session.commit()
        imported += len(batch)
    return imported


def import_tenders(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(OUTPUT_CSV))
    payload = [
        {
            "title": row.get("title", ""),
            "organization": row.get("organization", ""),
            "category": resolve_tender_category(
                title=row.get("title", ""),
                source=row.get("source", ""),
                raw_category=row.get("category", ""),
            ),
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
    count = upsert_with_presence(
        session,
        Tender,
        payload,
        "url",
        TENDER_CONTENT_COLUMNS,
        preserve_on_update=AI_PRESERVE_COLUMNS,
    )
    presence = sync_missing_from_source_counts(
        session,
        Tender,
        {row["url"] for row in payload},
    )
    closing = sync_closing_at_from_deadline(
        session,
        Tender,
        "closing_date",
        urls={row["url"] for row in payload},
    )
    print(
        f"[Import] Tenders: {count} rows; missing_from_source reset={presence['reset']} "
        f"incremented={presence['incremented']}; closing_at updated={closing['updated']}"
    )
    return count


def import_permits(session: Session, path: Path | None = None) -> int:
    csv_path = path or Path(BUILDING_PERMITS_CSV)
    if not csv_path.exists():
        print(f"[Import] Skipping missing file: {csv_path}")
        return 0

    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("address") or not row.get("external_id"):
                continue
            rows.append(
                {
                    "external_id": row.get("external_id", ""),
                    "address": row.get("address", ""),
                    "permit_type": row.get("permit_type", ""),
                    "project_value": row.get("project_value", ""),
                    "applicant": row.get("applicant", ""),
                    "issue_date": row.get("issue_date", ""),
                    "application_date": row.get("application_date", ""),
                    "description": row.get("description", ""),
                    "contractor": row.get("contractor", ""),
                    "local_area": row.get("local_area", ""),
                    "source": "vancouver",
                    "city": "Vancouver",
                }
            )

    imported = upsert_city_permits(
        session, rows, source="vancouver", full_refresh=False
    )
    print(f"[Import] Permits: {imported} rows (upsert)")
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
                "subreddit": row.get("subreddit", ""),
            }
        )
    count = _upsert_batch(session, RedditSignal, payload, "url")
    print(f"[Import] Reddit: {count} rows")
    return count


def import_news(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(NEWS_SIGNALS_CSV))
    payload = [
        {
            "title": row.get("title", ""),
            "text": row.get("text", ""),
            "publisher": row.get("publisher", ""),
            "date": row.get("date", ""),
            "url": row.get("url", ""),
        }
        for row in rows
        if row.get("url")
    ]
    count = _upsert_batch(session, NewsSignal, payload, "url")
    print(f"[Import] News: {count} rows")
    return count


def import_linkedin(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(LINKEDIN_SIGNALS_CSV))
    payload = []
    for row in rows:
        if not row.get("url"):
            continue
        try:
            likes = int(row.get("likes_count") or 0)
        except ValueError:
            likes = 0
        payload.append(
            {
                "title": row.get("title", ""),
                "content": row.get("content", ""),
                "author": row.get("author", ""),
                "date": row.get("date", ""),
                "url": row.get("url", ""),
                "likes_count": likes,
            }
        )
    count = _upsert_batch(session, LinkedInSignal, payload, "url")
    print(f"[Import] LinkedIn: {count} rows")
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


def import_commercial_tenders(session: Session, path: Path | None = None) -> int:
    rows = _read_csv(path or Path(COMMERCIAL_TENDERS_CSV))
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
            "source": row.get("source", ""),
        }
        for row in rows
        if row.get("url")
    ]
    count = upsert_with_presence(
        session,
        CommercialTender,
        payload,
        "url",
        COMMERCIAL_CONTENT_COLUMNS,
        preserve_on_update=AI_PRESERVE_COLUMNS,
        batch_size=COMMERCIAL_BATCH_SIZE,
    )
    presence = sync_missing_from_source_counts(
        session,
        CommercialTender,
        {row["url"] for row in payload},
    )
    closing = sync_closing_at_from_deadline(
        session,
        CommercialTender,
        "deadline",
        urls={row["url"] for row in payload},
    )
    print(
        f"[Import] Commercial tenders: {count} rows; "
        f"missing_from_source reset={presence['reset']} incremented={presence['incremented']}; "
        f"closing_at updated={closing['updated']}"
    )
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
    count = upsert_with_presence(
        session,
        ArchTender,
        payload,
        "url",
        ARCH_CONTENT_COLUMNS,
        preserve_on_update=AI_PRESERVE_COLUMNS,
    )
    presence = sync_missing_from_source_counts(
        session,
        ArchTender,
        {row["url"] for row in payload},
    )
    closing = sync_closing_at_from_deadline(
        session,
        ArchTender,
        "deadline",
        urls={row["url"] for row in payload},
    )
    print(
        f"[Import] Architecture tenders: {count} rows; "
        f"missing_from_source reset={presence['reset']} incremented={presence['incremented']}; "
        f"closing_at updated={closing['updated']}"
    )
    return count


def import_all_csvs(
    session: Session, *, skip: frozenset[str] = frozenset()
) -> dict[str, int | bool]:
    """
    (Stage 2) ``skip`` names import keys (matching this function's own
    return-dict keys: "tenders", "arch_tenders", "commercial_tenders")
    whose owning scraper step did not succeed this run. A skipped
    source's import_X() function is never called at all -- its CSV is
    never read, its table's ON CONFLICT upsert never runs, and its rows'
    missing_from_source_count/last_seen_at are left completely
    untouched. The skip is recorded explicitly
    (result[f"{key}_skipped"] = True) rather than silently omitted.
    Defaults to an empty set, which reproduces today's exact
    "import everything" behavior for any caller that doesn't pass it.

    import_tenders()/import_arch_tenders()/import_commercial_tenders()
    themselves are untouched by this change -- the skip decision is made
    entirely at this call site.
    """
    print("[Import] Starting CSV import into PostgreSQL")
    result: dict[str, int | bool] = {}

    if "tenders" in skip:
        result["tenders"] = 0
        result["tenders_skipped"] = True
    else:
        result["tenders"] = import_tenders(session)

    result["permits"] = import_permits(session)
    result["reddit"] = import_reddit(session)
    result["news"] = import_news(session)
    result["linkedin"] = import_linkedin(session)
    result["jobs"] = import_jobs(session)

    if "arch_tenders" in skip:
        result["arch_tenders"] = 0
        result["arch_tenders_skipped"] = True
    else:
        result["arch_tenders"] = import_arch_tenders(session)

    if "commercial_tenders" in skip:
        result["commercial_tenders"] = 0
        result["commercial_tenders_skipped"] = True
    else:
        result["commercial_tenders"] = import_commercial_tenders(session)

    return result
