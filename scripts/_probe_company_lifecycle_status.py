"""Read-only production distribution: companies.lifecycle_status."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import defaultdict

from sqlalchemy import text

from db.connection import get_engine
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name

QUERY = text(
    """
    SELECT lifecycle_status, is_operating, COUNT(*) AS n
    FROM companies
    GROUP BY lifecycle_status, is_operating
    ORDER BY n DESC
    """
)


def main() -> None:
    guard_readonly_db(_SCRIPT)
    with get_engine().connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0
        rows = conn.execute(QUERY).all()

    print("=== companies lifecycle_status distribution (production) ===")
    print(f"total companies: {total:,}\n")
    print(f"{'lifecycle_status':<28} {'is_operating':<14} count")
    print("-" * 52)
    for row in rows:
        print(f"{row.lifecycle_status:<28} {str(row.is_operating):<14} {row.n:,}")

    by_status: dict[str, int] = defaultdict(int)
    for row in rows:
        by_status[row.lifecycle_status] += row.n

    print("\n=== by lifecycle_status ===")
    for key in ("active", "quiet", "dormant", "no_observable_activity"):
        count = by_status.get(key, 0)
        pct = count / total * 100 if total else 0
        print(f"  {key}: {count:,} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
