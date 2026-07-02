#!/usr/bin/env python3
"""Local verification for P2-01 lifecycle schema (pre-deploy review)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.models import ArchTender, CommercialTender, Tender
from db.tender_lifecycle_ddl import TENDER_LIFECYCLE_TABLES
from sqlalchemy import func, inspect, select, text


def main() -> int:
    init_db(raise_on_failure=True)
    session = get_session()
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "tables": {}}

    try:
        inspector = inspect(session.bind)
        for table in TENDER_LIFECYCLE_TABLES:
            cols = {c["name"] for c in inspector.get_columns(table)}
            lifecycle_cols = {
                name
                for name in cols
                if name.startswith("lifecycle")
                or name in {
                    "is_open",
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
            }
            model = {"tenders": Tender, "commercial_tenders": CommercialTender, "arch_tenders": ArchTender}[table]
            total = session.scalar(select(func.count()).select_from(model)) or 0
            active = session.scalar(
                select(func.count()).select_from(model).where(model.lifecycle_status == "active")
            ) or 0
            open_count = session.scalar(
                select(func.count()).select_from(model).where(model.is_open.is_(True))
            ) or 0
            report["tables"][table] = {
                "row_count": total,
                "lifecycle_column_count": len(lifecycle_cols),
                "lifecycle_status_active": active,
                "is_open_true": open_count,
                "indexes": [idx["name"] for idx in inspector.get_indexes(table) if "lifecycle" in idx["name"] or "is_open" in idx["name"] or "closing_at" in idx["name"]],
            }

        dupes = session.execute(
            text(
                """
                SELECT 'tenders' AS tbl, COUNT(*) AS cnt FROM (
                    SELECT url FROM tenders GROUP BY url HAVING COUNT(*) > 1
                ) d
                UNION ALL
                SELECT 'commercial_tenders', COUNT(*) FROM (
                    SELECT url FROM commercial_tenders GROUP BY url HAVING COUNT(*) > 1
                ) d
                UNION ALL
                SELECT 'arch_tenders', COUNT(*) FROM (
                    SELECT url FROM arch_tenders GROUP BY url HAVING COUNT(*) > 1
                ) d
                """
            )
        ).all()
        report["duplicate_urls"] = {row[0]: row[1] for row in dupes}
        report["pass"] = all(
            table["row_count"] >= 0 and table["lifecycle_column_count"] >= 17
            for table in report["tables"].values()
        )
    finally:
        session.close()

    print(json.dumps(report, indent=2))
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
