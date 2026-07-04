"""Apply Phase 4A permit column migration to the configured DATABASE_URL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import get_engine
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args

_SCRIPT = Path(__file__).name

STATEMENTS = (
    "ALTER TABLE permits ADD COLUMN IF NOT EXISTS application_date VARCHAR(20) DEFAULT ''",
    "ALTER TABLE permits ADD COLUMN IF NOT EXISTS contractor VARCHAR(300) DEFAULT ''",
    "ALTER TABLE permits ADD COLUMN IF NOT EXISTS local_area VARCHAR(100) DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS ix_permits_application_date "
    "ON permits (application_date) WHERE application_date <> ''",
    "CREATE INDEX IF NOT EXISTS ix_permits_local_area "
    "ON permits (local_area) WHERE local_area <> ''",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_production_safety_args(parser)
    args = parser.parse_args()
    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="permit migration")

    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
            print(f"OK: {stmt[:70]}...")

    with engine.connect() as conn:
        cols = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'permits'
                  AND column_name IN ('application_date', 'contractor', 'local_area')
                ORDER BY column_name
                """
            )
        ).fetchall()
    print("COLUMNS:", [row[0] for row in cols])


if __name__ == "__main__":
    main()
