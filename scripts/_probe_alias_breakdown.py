"""Break down alias count: plan 1440 vs applied 1993."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import check_db_connection, get_session
from db.db_safety import guard_readonly_db
from pipeline.company_canonical_merge import build_merge_plan, safe_merge_groups

_SCRIPT = Path(__file__).name


def main() -> None:
    guard_readonly_db(_SCRIPT)
    if not check_db_connection():
        raise SystemExit(1)
    session = get_session()
    try:
        plan = build_merge_plan(session)
        safe = safe_merge_groups(plan)

        planned_aliases = sum(len(g.members) - 1 for g in safe)
        create_groups = [g for g in safe if g.create_canonical_row]
        reuse_primary = [g for g in safe if not g.create_canonical_row]

        # When create_canonical_row=True, apply inserts NEW canonical; ALL members become aliases.
        applied_aliases_if_all_members = sum(len(g.members) for g in create_groups) + sum(
            len(g.members) - 1 for g in reuse_primary
        )

        print("=== ALIAS COUNT BREAKDOWN ===")
        print(f"safe merge groups: {len(safe)}")
        print(f"plan safe_alias_count (sum n-1): {planned_aliases}")
        print(f"groups with create_canonical_row=True: {len(create_groups)}")
        print(f"groups reusing existing primary: {len(reuse_primary)}")
        print(
            f"expected applied aliases (create: all members + reuse: n-1): "
            f"{applied_aliases_if_all_members}"
        )
        print(f"extra aliases vs plan from create_canonical_row: {applied_aliases_if_all_members - planned_aliases}")

        db_alias = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE entity_role = 'applicant_alias'")
        ).scalar_one()
        db_canonical = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE entity_role = 'canonical'")
        ).scalar_one()
        db_probable = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE entity_role = 'probable_person'")
        ).scalar_one()
        db_aliases_from_run = session.execute(
            text("SELECT COUNT(*) FROM company_applicant_aliases WHERE merge_run_id = 1")
        ).scalar_one()
        print(f"\nDB applicant_alias: {db_alias}")
        print(f"DB canonical: {db_canonical}")
        print(f"DB probable_person: {db_probable}")
        print(f"DB company_applicant_aliases (run 1): {db_aliases_from_run}")

        print("\n=== COMPANY 3046 ===")
        row = session.execute(
            text(
                """
                SELECT id, name, entity_role, display_name, canonical_company_id,
                       total_projects, total_value, total_award_value
                FROM companies WHERE id = 3046
                """
            )
        ).first()
        print(dict(row._mapping))
        print(
            "permits:",
            session.execute(text("SELECT COUNT(*) FROM permits WHERE company_id=3046")).scalar_one(),
        )
        print(
            "awards:",
            session.execute(text("SELECT COUNT(*) FROM contract_awards WHERE company_id=3046")).scalar_one(),
        )

        # Confirm 3046 was NOT in any safe merge group
        in_safe = any(any(m.company_id == 3046 for m in g.members) for g in safe)
        print(f"\n3046 in safe merge group: {in_safe}")

        # Excluded groups containing 3046?
        all_multi = [g for g in plan.groups if len(g.members) > 1]
        for g in all_multi:
            if any(m.company_id == 3046 for m in g.members):
                print(f"3046 in group key={g.canonical_key} display={g.display_name} members={len(g.members)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
