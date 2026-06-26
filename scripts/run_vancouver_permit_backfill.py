"""Run full Vancouver permit backfill and print before/after DB stats."""

from __future__ import annotations

from sqlalchemy import text

from db.connection import get_engine, get_session, init_db
from scraper.building_permits import scrape_vancouver_permits


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
