"""Run full Vancouver permit backfill and print before/after DB stats."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import get_session, init_db
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args
from scraper.building_permits import scrape_vancouver_permits

_SCRIPT = Path(__file__).name


def _stats() -> dict:
    session = get_session()
    try:
        row = session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE external_id <> '') AS with_external_id,
                    COUNT(*) FILTER (WHERE application_date <> '') AS with_application_date,
                    COUNT(*) FILTER (WHERE contractor <> '') AS with_contractor,
                    COUNT(*) FILTER (WHERE local_area <> '') AS with_local_area
                FROM permits
                WHERE source = 'vancouver'
                """
            )
        ).one()
        return dict(row._mapping)
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_production_safety_args(parser)
    args = parser.parse_args()
    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="vancouver permit backfill")

    init_db()
    print("BEFORE", _stats())
    result = scrape_vancouver_permits(days=None, persist=True)
    print("SCRAPE", result)
    print("AFTER", _stats())

    session = get_session()
    try:
        samples = session.execute(
            text(
                """
                SELECT external_id, application_date, issue_date, contractor, local_area, project_value
                FROM permits
                WHERE source = 'vancouver' AND application_date <> ''
                ORDER BY application_date DESC
                LIMIT 5
                """
            )
        ).fetchall()
        print("SAMPLE")
        for row in samples:
            print(dict(row._mapping))
    finally:
        session.close()


if __name__ == "__main__":
    main()
