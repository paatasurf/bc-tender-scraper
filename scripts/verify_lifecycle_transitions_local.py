#!/usr/bin/env python3
"""Local-only verification for P2-02 lifecycle transitions."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select, text

from db.connection import init_db
from db.lifecycle_constants import (
    LIFECYCLE_STATUS_CLOSED,
    LIFECYCLE_STATUS_CLOSING_SOON,
    LIFECYCLE_STATUS_DELISTED,
)
from db.models import ArchTender, CommercialTender, Tender
from pipeline.lifecycle_resolver import resolve_tender_lifecycle


def _require_local_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        raise SystemExit("Refusing P2-02 verification against production DATABASE_URL")
    return database_url


def _status_counts(session, model) -> dict[str, int]:
    rows = session.execute(
        select(model.lifecycle_status, func.count())
        .group_by(model.lifecycle_status)
        .order_by(model.lifecycle_status)
    ).all()
    return {status: count for status, count in rows}


def main() -> int:
    _require_local_database_url()
    init_db(raise_on_failure=True)

    from db.connection import get_session

    session = get_session()
    try:
        before = {
            "tenders": _status_counts(session, Tender),
            "commercial_tenders": _status_counts(session, CommercialTender),
            "arch_tenders": _status_counts(session, ArchTender),
        }
        summary = resolve_tender_lifecycle(session, now=datetime.now(timezone.utc))
        after = {
            "tenders": _status_counts(session, Tender),
            "commercial_tenders": _status_counts(session, CommercialTender),
            "arch_tenders": _status_counts(session, ArchTender),
        }

        payload = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "before": before,
            "after": after,
            "resolve_summary": summary,
            "spot_checks": {
                "closed": session.scalar(
                    select(func.count()).select_from(Tender).where(
                        Tender.lifecycle_status == LIFECYCLE_STATUS_CLOSED
                    )
                ),
                "closing_soon": session.scalar(
                    select(func.count()).select_from(Tender).where(
                        Tender.lifecycle_status == LIFECYCLE_STATUS_CLOSING_SOON
                    )
                ),
                "delisted": session.scalar(
                    select(func.count()).select_from(Tender).where(
                        Tender.lifecycle_status == LIFECYCLE_STATUS_DELISTED
                    )
                ),
            },
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
