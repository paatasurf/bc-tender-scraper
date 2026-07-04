"""Read-only: verify lifecycle fields for unified opportunities sample."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
from datetime import datetime, timezone
from urllib.request import urlopen

from sqlalchemy import text

from db.connection import get_engine
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name

API_URL = (
    "https://bc-tender-scraper-production.up.railway.app"
    "/api/companies/8638/opportunities/unified?limit=10"
)

BATCH_SQL = text(
    """
    WITH wanted(id, ord) AS (
      SELECT * FROM unnest(CAST(:ids AS int[])) WITH ORDINALITY AS t(id, ord)
    ),
    found AS (
      SELECT 'tenders'::text AS src, t.id, t.closing_at, t.lifecycle_status, t.is_open
      FROM tenders t JOIN wanted w ON w.id = t.id
      UNION ALL
      SELECT 'commercial_tenders', t.id, t.closing_at, t.lifecycle_status, t.is_open
      FROM commercial_tenders t JOIN wanted w ON w.id = t.id
      UNION ALL
      SELECT 'arch_tenders', t.id, t.closing_at, t.lifecycle_status, t.is_open
      FROM arch_tenders t JOIN wanted w ON w.id = t.id
    )
    SELECT w.ord, w.id AS tender_id, f.src, f.is_open, f.lifecycle_status, f.closing_at
    FROM wanted w
    LEFT JOIN found f ON f.id = w.id
    ORDER BY w.ord
    """
)


def _fetch_unified_ids() -> list[int]:
    with urlopen(API_URL, timeout=120) as response:
        payload = json.loads(response.read().decode())
    return [int(item["tender_id"]) for item in payload.get("items") or []]


def main() -> None:
    guard_readonly_db(_SCRIPT)
    now = datetime.now(timezone.utc)
    tender_ids = _fetch_unified_ids()
    print(f"Unified API returned {len(tender_ids)} tender_ids for company 8638")
    print(f"Reference now (UTC): {now.isoformat()}\n")
    print(
        f"{'#':<3} {'tender_id':<8} {'table':<20} {'is_open':<8} "
        f"{'lifecycle_status':<16} closing_at (UTC)                  past?"
    )
    print("-" * 95)

    all_open = True
    none_past = True

    with get_engine().connect() as conn:
        rows = conn.execute(BATCH_SQL, {"ids": tender_ids}).all()

    for row in rows:
        closing_at = row.closing_at
        is_open = row.is_open
        past = False
        closing_display = ""
        if closing_at is not None:
            if closing_at.tzinfo is None:
                closing_at = closing_at.replace(tzinfo=timezone.utc)
            else:
                closing_at = closing_at.astimezone(timezone.utc)
            closing_display = closing_at.isoformat()
            past = closing_at <= now

        if not is_open:
            all_open = False
        if past:
            none_past = False

        table = row.src or "NOT FOUND"
        print(
            f"{int(row.ord):<3} {row.tender_id:<8} {table:<20} {str(is_open):<8} "
            f"{(row.lifecycle_status or ''):<16} {closing_display:<33} {past}"
        )

    print()
    print(f"CONFIRM all is_open=true: {all_open}")
    print(f"CONFIRM no closing_at in past: {none_past}")


if __name__ == "__main__":
    main()
