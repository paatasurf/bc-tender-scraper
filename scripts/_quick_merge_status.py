"""Quick snapshot while merge apply is running."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name


def main() -> None:
    guard_readonly_db(_SCRIPT)
    session = get_session()
    try:
        for label, sql in [
            ("merge_runs", "SELECT id, status, dry_run FROM company_canonical_merge_runs ORDER BY id DESC LIMIT 3"),
            ("entity_role", """
                SELECT COALESCE(entity_role,'unset') AS r, COUNT(*) FROM companies GROUP BY 1 ORDER BY 2 DESC
            """),
            ("permits_linked", "SELECT COUNT(*) FROM permits WHERE company_id IS NOT NULL"),
            ("aliases", "SELECT COUNT(*) FROM companies WHERE entity_role='applicant_alias'"),
            ("canonical", "SELECT COUNT(*) FROM companies WHERE entity_role='canonical'"),
        ]:
            print(f"\n=== {label} ===")
            for row in session.execute(text(sql)).all():
                print(dict(row._mapping))
    finally:
        session.close()


if __name__ == "__main__":
    main()
