#!/usr/bin/env python3
"""Backfill closing_at from string deadline columns (P2-06). LOCAL DATABASE ONLY."""

from __future__ import annotations

import config.env  # noqa: F401  — load DATABASE_URL from .env

import json
import os
import sys
from datetime import datetime, timezone

from db.closing_at_sync import backfill_all_tender_closing_at
from db.connection import get_session, init_db


def _require_local_database_url() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        raise SystemExit("Refusing P2-06 backfill against production DATABASE_URL")


def main() -> int:
    _require_local_database_url()
    init_db(raise_on_failure=True)
    session = get_session()
    try:
        summary = backfill_all_tender_closing_at(session, only_null=True)
        payload = {
            "backfilled_at": datetime.now(timezone.utc).isoformat(),
            "tables": summary,
            "totals": {
                "updated": sum(item["updated"] for item in summary.values()),
                "after_set": sum(item["after_set"] for item in summary.values()),
                "after_null": sum(item["after_null"] for item in summary.values()),
            },
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
