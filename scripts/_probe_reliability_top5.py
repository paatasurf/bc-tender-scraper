from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


"""Top-5 permit companies reliability sample (read-only)."""
from sqlalchemy import text
from db.connection import get_engine
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name

with get_engine().connect() as c:
    top = c.execute(
        text(
            """
            SELECT c.id, c.name, COUNT(p.id) AS n
            FROM companies c
            JOIN permits p ON p.applicant = c.name AND p.applicant <> ''
            GROUP BY c.id, c.name
            ORDER BY n DESC
            LIMIT 5
            """
        )
    ).all()
    for row in top:
        stats = c.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE p.is_active) AS active_n,
                  COUNT(*) FILTER (WHERE NOT p.is_active) AS inactive_n,
                  COUNT(*) FILTER (WHERE p.lifecycle_status = 'stale') AS stale_n,
                  COUNT(*) FILTER (WHERE p.lifecycle_status = 'active') AS active_status_n,
                  COUNT(*) FILTER (WHERE p.source_status_raw <> '') AS has_src,
                  MIN(p.issue_date) FILTER (WHERE p.is_active AND p.issue_date ~ '^[0-9]{4}-')
                    AS oldest_active,
                  MAX(p.issue_date) FILTER (WHERE p.is_active AND p.issue_date ~ '^[0-9]{4}-')
                    AS newest_active
                FROM permits p
                WHERE p.applicant = :name AND p.applicant <> ''
                """
            ),
            {"name": row.name},
        ).one()
        lc = c.execute(
            text("SELECT lifecycle_status, is_operating FROM companies WHERE id = :id"),
            {"id": row.id},
        ).one()
        print(f"{row.name} (id={row.id}, permits={row.n})")
        print(f"  company lifecycle: {lc.lifecycle_status} operating={lc.is_operating}")
        print(
            f"  permits active/inactive: {stats.active_n}/{stats.inactive_n} "
            f"status active/stale: {stats.active_status_n}/{stats.stale_n} "
            f"source_status rows: {stats.has_src}"
        )
        print(f"  active issue_date range: {stats.oldest_active} .. {stats.newest_active}")
