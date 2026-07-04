"""Production data inventory for capability audit (read-only)."""
from __future__ import annotations


from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import get_engine
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name

TABLES = [
    "tenders",
    "commercial_tenders",
    "arch_tenders",
    "permits",
    "companies",
    "arch_companies",
    "contract_awards",
    "tender_outcomes",
    "early_signal_events",
    "project_contacts",
    "tender_matches",
    "company_wiki",
    "reddit",
    "news",
    "linkedin_signals",
    "jobs",
    "client_profiles",
    "pipeline_runs",
]


def main() -> None:
    guard_readonly_db(_SCRIPT)
    with get_engine().connect() as conn:
        print("=== TABLE ROW COUNTS ===")
        for table in TABLES:
            try:
                n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"  {table}: {n:,}")
            except Exception as exc:
                print(f"  {table}: ERROR {exc}")

        print("\n=== LIFECYCLE SNAPSHOTS ===")
        for label, sql in [
            ("tenders lifecycle", "SELECT lifecycle_status, is_open, COUNT(*) n FROM tenders GROUP BY 1,2 ORDER BY n DESC LIMIT 8"),
            ("permits lifecycle", "SELECT lifecycle_status, is_active, COUNT(*) n FROM permits GROUP BY 1,2 ORDER BY n DESC"),
            ("companies lifecycle", "SELECT lifecycle_status, is_operating, COUNT(*) n FROM companies GROUP BY 1,2 ORDER BY n DESC"),
            ("tenders with closing_at", "SELECT COUNT(*) FILTER (WHERE closing_at IS NOT NULL), COUNT(*) FROM tenders"),
            ("awards with company_id", "SELECT COUNT(*) FILTER (WHERE company_id IS NOT NULL), COUNT(*) FROM contract_awards"),
            ("tender_outcomes", "SELECT outcome, COUNT(*) FROM tender_outcomes GROUP BY 1"),
            ("project_contacts by type", "SELECT project_type, COUNT(*) FROM project_contacts GROUP BY 1 ORDER BY 2 DESC"),
            ("early_signal_events", "SELECT source, COUNT(*) FROM early_signal_events GROUP BY 1 ORDER BY 2 DESC LIMIT 5"),
        ]:
            print(f"\n  {label}:")
            try:
                for row in conn.execute(text(sql)).all():
                    print(f"    {row}")
            except Exception as exc:
                print(f"    ERROR {exc}")

        print("\n=== DATE COVERAGE (samples) ===")
        for label, sql in [
            ("tenders first_seen", "SELECT MIN(first_seen_at), MAX(first_seen_at), COUNT(*) FILTER (WHERE first_seen_at IS NOT NULL) FROM tenders"),
            ("permits issue_date range", "SELECT MIN(issue_date), MAX(issue_date), COUNT(*) FILTER (WHERE issue_date <> '') FROM permits"),
            ("awards date range", "SELECT MIN(award_date), MAX(award_date) FROM contract_awards WHERE award_date <> ''"),
            ("companies last_activity", "SELECT COUNT(*) FILTER (WHERE last_activity_at IS NOT NULL), COUNT(*) FROM companies"),
        ]:
            print(f"  {label}: {conn.execute(text(sql)).one()}")


if __name__ == "__main__":
    main()
