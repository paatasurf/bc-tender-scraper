#!/usr/bin/env python3
"""Quick probe for DB-backed enterprise seed prerequisites."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401
from sqlalchemy import func, or_, select

from db.connection import check_db_connection, get_session
from db.db_safety import guard_readonly_db
from db.models import Company, OdbusReference, OrgbookReference

_SCRIPT = Path(__file__).name


def _timed(label: str):
    t0 = time.perf_counter()

    def done(msg: str = ""):
        elapsed = time.perf_counter() - t0
        print(f"[{label}] {msg} ({elapsed:.1f}s)")

    return done


def main() -> int:
    guard_readonly_db(_SCRIPT)

    print("[probe] starting", flush=True)
    fin = _timed("check_db_connection")
    ok = check_db_connection()
    fin(f"ok={ok}")
    if not ok:
        return 1

    session = get_session()
    try:
        for label, stmt in [
            (
                "tier_a_count",
                select(func.count()).select_from(Company).where(Company.company_tier == "tier_a"),
            ),
            (
                "canonical_strong_count",
                select(func.count()).select_from(Company).where(
                    (Company.entity_role == "canonical")
                    & (
                        (Company.award_count >= 2)
                        | (Company.total_award_value >= 3_000_000)
                        | (Company.total_projects >= 25)
                        | (Company.total_value >= 10_000_000)
                    )
                ),
            ),
            (
                "odb_naics23_bc",
                select(func.count()).select_from(OdbusReference).where(
                    OdbusReference.province.in_(["BC", "British Columbia"]),
                    or_(
                        OdbusReference.derived_naics.like("23%"),
                        OdbusReference.source_naics.like("23%"),
                    ),
                ),
            ),
            ("orgbook_total", select(func.count()).select_from(OrgbookReference)),
            (
                "orgbook_bc",
                select(func.count()).select_from(OrgbookReference).where(OrgbookReference.province == "BC"),
            ),
        ]:
            fin = _timed(label)
            count = session.scalar(stmt)
            fin(f"count={count}")
    finally:
        session.close()

    print("[probe] complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
