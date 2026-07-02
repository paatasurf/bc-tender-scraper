"""Simulate first resolve-permits run (read-only) using resolver logic."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from db.connection import get_session
from db.models import Permit
from pipeline.permit_lifecycle_resolver import (
    PermitLifecycleSnapshot,
    evaluate_permit_lifecycle_transition,
)

REF = datetime(2026, 7, 2, tzinfo=timezone.utc)


def main() -> None:
    totals: dict[str, int] = {}
    by_source: dict[str, dict[str, int]] = {}

    with get_session() as s:
        for row in s.scalars(select(Permit)):
            snap = PermitLifecycleSnapshot(
                lifecycle_status=row.lifecycle_status,
                is_active=row.is_active,
                lifecycle_status_override=row.lifecycle_status_override,
                source_status_raw=row.source_status_raw or "",
                issue_date=row.issue_date or "",
                application_date=row.application_date or "",
            )
            rule = evaluate_permit_lifecycle_transition(snap, now=REF)
            key = rule or "skipped_no_change"
            totals[key] = totals.get(key, 0) + 1
            src = row.source or "unknown"
            by_source.setdefault(src, {})
            by_source[src][key] = by_source.get(src, {}).get(key, 0) + 1

    print("=== FIRST RESOLVE SIMULATION (2026-07-02, source_status_raw empty) ===")
    print("totals:")
    for k, v in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print("\nby source:")
    for src in sorted(by_source):
        print(f"  {src}:")
        for k, v in sorted(by_source[src].items(), key=lambda x: -x[1]):
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
