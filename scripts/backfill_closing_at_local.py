#!/usr/bin/env python3
"""Backfill closing_at from string deadline columns (P2-06). LOCAL DATABASE ONLY."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401

from db.closing_at_sync import backfill_all_tender_closing_at
from db.connection import get_session, init_db
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args

_SCRIPT = Path(__file__).name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_production_safety_args(parser)
    args = parser.parse_args()
    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="closing_at backfill")

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
