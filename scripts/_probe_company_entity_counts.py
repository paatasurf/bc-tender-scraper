"""Read-only probe: company entity_role counts and merge apply status."""

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
        total = session.execute(text("SELECT COUNT(*) AS n FROM companies")).scalar_one()
        print(f"total_companies: {total}")

        print("\nentity_role breakdown:")
        for row in session.execute(
            text(
                """
                SELECT COALESCE(entity_role, 'unset') AS entity_role, COUNT(*) AS n
                FROM companies
                GROUP BY 1
                ORDER BY n DESC
                """
            )
        ).all():
            print(f"  {row.entity_role}: {row.n}")

        alias_fk = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE canonical_company_id IS NOT NULL")
        ).scalar_one()
        print(f"\ncanonical_company_id IS NOT NULL: {alias_fk}")

        permits = session.execute(
            text("SELECT COUNT(*) FROM permits WHERE company_id IS NOT NULL")
        ).scalar_one()
        print(f"permits.company_id IS NOT NULL: {permits}")

        runs = session.execute(text("SELECT COUNT(*) FROM company_canonical_merge_runs")).scalar_one()
        applied = session.execute(
            text(
                """
                SELECT COUNT(*) FROM company_canonical_merge_runs
                WHERE status = 'applied' AND dry_run = false
                """
            )
        ).scalar_one()
        print(f"\nmerge_runs total: {runs}, applied (non-dry): {applied}")

        pontem = session.execute(
            text(
                """
                SELECT id, name, entity_role, canonical_company_id, display_name
                FROM companies WHERE id = 8638
                """
            )
        ).first()
        print(f"\ncompany id=8638: {dict(pontem._mapping) if pontem else 'NOT FOUND'}")

        # Expected post-safe-apply math
        safe_alias = 1440
        safe_create = 553
        print("\n--- projected after safe --apply (not yet run) ---")
        print(f"rows unchanged (no DELETE): {total}")
        print(f"visible if hide alias+probable_person: ~{total - safe_alias - 157}")
        print(f"  (also +{safe_create} new canonical rows if created -> ~{total - safe_alias - 157 + safe_create})")
    finally:
        session.close()


if __name__ == "__main__":
    main()
