from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


"""Supplementary company lifecycle counts."""
from sqlalchemy import text
from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
with get_session() as s:
    items = [
        ("arch_companies", "SELECT COUNT(*) FROM arch_companies"),
        (
            "tender_matches construction companies",
            "SELECT COUNT(DISTINCT company_id) FROM tender_matches WHERE company_kind = 'construction'",
        ),
        (
            "tender_matches arch companies",
            "SELECT COUNT(DISTINCT company_id) FROM tender_matches WHERE company_kind = 'architecture'",
        ),
        ("companies total_projects=0", "SELECT COUNT(*) FROM companies WHERE total_projects = 0"),
        (
            "companies permit-only (projects>0, awards=0)",
            "SELECT COUNT(*) FROM companies WHERE total_projects > 0 AND award_count = 0",
        ),
        (
            "companies award-only (projects=0, awards>0)",
            "SELECT COUNT(*) FROM companies WHERE total_projects = 0 AND award_count > 0",
        ),
        (
            "unknown lifecycle + empty last_project_date",
            "SELECT COUNT(*) FROM companies WHERE company_lifecycle = 'unknown' AND last_project_date = ''",
        ),
        (
            "unknown lifecycle + has last_award_date",
            "SELECT COUNT(*) FROM companies WHERE company_lifecycle = 'unknown' AND last_award_date <> ''",
        ),
        (
            "no exact permit applicant match",
            """
            SELECT COUNT(*) FROM companies c
            WHERE NOT EXISTS (
                SELECT 1 FROM permits p WHERE p.applicant = c.name AND p.applicant <> ''
            )
            """,
        ),
        (
            "zero projects and zero awards",
            "SELECT COUNT(*) FROM companies WHERE total_projects = 0 AND award_count = 0",
        ),
        (
            "last_enriched_at set",
            "SELECT COUNT(*) FROM companies WHERE last_enriched_at IS NOT NULL",
        ),
    ]
    for label, sql in items:
        print(f"{label}: {s.scalar(text(sql)):,}")
