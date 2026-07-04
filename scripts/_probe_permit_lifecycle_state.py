"""Read-only production probe: current permit lifecycle distribution."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import datetime, timezone

from sqlalchemy import select, text

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Permit
from pipeline.permit_lifecycle_resolver import (
    PermitLifecycleSnapshot,
    evaluate_permit_lifecycle_transition,
)
now = datetime.now(timezone.utc)

with get_session() as s:
    print("=== lifecycle_status x is_active (production) ===")
    rows = s.execute(
        text(
            """
            SELECT lifecycle_status, is_active, COUNT(*) AS n
            FROM permits
            GROUP BY lifecycle_status, is_active
            ORDER BY n DESC
            """
        )
    ).all()
    total = 0
    for r in rows:
        print(f"  {r.lifecycle_status!r} is_active={r.is_active}: {r.n:,}")
        total += r.n
    print(f"TOTAL: {total:,}")

    print("\n=== resolver simulation NOW (same logic as production) ===")
    totals: dict[str, int] = {}
    for row in s.scalars(select(Permit)):
        snap = PermitLifecycleSnapshot(
            lifecycle_status=row.lifecycle_status,
            is_active=row.is_active,
            lifecycle_status_override=row.lifecycle_status_override,
            source_status_raw=row.source_status_raw or "",
            issue_date=row.issue_date or "",
            application_date=row.application_date or "",
        )
        rule = evaluate_permit_lifecycle_transition(snap, now=now)
        key = rule or "skipped_no_change"
        totals[key] = totals.get(key, 0) + 1
    for k, v in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v:,}")
