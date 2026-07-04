"""Probe Ledcor/Pontem merge groups and FK references to 8638."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company
from pipeline.company_canonical_merge import build_merge_plan
from pipeline.company_classification import parse_name
from pipeline.company_matching import normalize_vendor_name


def main() -> None:
    guard_readonly_db(_SCRIPT)
    session = get_session()
    try:
        rows = session.execute(
            select(
                Company.id,
                Company.name,
                Company.total_projects,
                Company.total_value,
                Company.total_award_value,
                Company.award_count,
            ).where(Company.name.ilike("%ledcor%"))
        ).all()
        print(f"All Ledcor-like companies: {len(rows)}")
        for row in sorted(rows, key=lambda r: -(float(r.total_value or 0))):
            parsed = parse_name(row.name)
            trade = parsed["dba"] or parsed["legal"]
            key = normalize_vendor_name(trade)
            print(
                f"id={row.id} projects={row.total_projects} value={row.total_value} "
                f"awards={row.total_award_value} cnt={row.award_count} key={key} | {row.name[:95]}"
            )

        plan = build_merge_plan(session)
        for keyword in ("pontem", "ledcor"):
            for group in plan.groups:
                if len(group.members) <= 1:
                    continue
                if keyword not in group.canonical_key and keyword not in group.display_name.lower():
                    continue
                print(f"\n=== MERGE PLAN: {group.display_name} ===")
                print(
                    f"primary_id={group.primary_company_id} create_row={group.create_canonical_row} "
                    f"insert={group.canonical_name_for_insert!r}"
                )
                for member in sorted(group.members, key=lambda m: -m.total_value):
                    role = "CANONICAL" if member.company_id == group.primary_company_id else "ALIAS"
                    print(f"  [{role}] id={member.company_id} {member.name[:80]}")

        fk_tables = [
            ("client_profiles", "company_id"),
            ("company_wiki", "company_id"),
            ("tender_outcomes", "company_id"),
            ("tender_matches", "company_id"),
            ("contract_awards", "company_id"),
            ("google_enrichment_logs", "company_id"),
        ]
        print("\n=== FK refs to company_id=8638 ===")
        for table, col in fk_tables:
            try:
                count = session.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {col} = 8638")
                ).scalar_one()
                print(f"  {table}.{col}: {count}")
            except Exception as exc:
                print(f"  {table}: skip ({exc.__class__.__name__})")

        alias_ids = [7436, 4497]
        print("\n=== FK refs to Pontem alias ids 7436, 4497 ===")
        for aid in alias_ids:
            for table, col in fk_tables[:3]:
                try:
                    count = session.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :id"),
                        {"id": aid},
                    ).scalar_one()
                    if count:
                        print(f"  {table} id={aid}: {count}")
                except Exception:
                    pass
    finally:
        session.close()


if __name__ == "__main__":
    main()
