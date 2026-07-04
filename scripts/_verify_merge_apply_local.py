"""Step-by-step local verification for company canonical merge."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company
from pipeline.company_resolution import CompanyResolver


def header(step: str, title: str) -> None:
    print(f"\n{'='*70}")
    print(f"STEP {step}: {title}")
    print("=" * 70)


def step_migrations(session) -> None:
    header("1", "Migration 014+015 columns present")
    cols = session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'companies'
              AND column_name IN (
                'display_name','entity_role','canonical_company_id',
                'applicant_signatory','canonical_merge_confidence','canonical_merge_method'
              )
            ORDER BY 1
            """
        )
    ).scalars().all()
    print("companies columns:", list(cols))
    pcols = session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'permits'
              AND column_name IN ('company_id','canonical_merge_confidence','canonical_merge_method')
            ORDER BY 1
            """
        )
    ).scalars().all()
    print("permits columns:", list(pcols))
    runs = session.execute(
        text("SELECT to_regclass('company_canonical_merge_runs') IS NOT NULL")
    ).scalar_one()
    print("audit tables exist:", bool(runs))


def step_apply_status(session) -> None:
    header("2", "Merge apply status")
    row = session.execute(
        text(
            """
            SELECT id, status, dry_run, started_at, finished_at,
                   summary_json->>'applied_merge_groups' AS groups,
                   summary_json->>'applied_permit_assignments' AS permits,
                   summary_json->>'probable_person_marked' AS probable
            FROM company_canonical_merge_runs
            ORDER BY id DESC LIMIT 1
            """
        )
    ).first()
    print(dict(row._mapping) if row else "NO RUNS")
    fk = session.execute(
        text(
            """
            SELECT summary_json->'fk_remap' AS fk
            FROM company_canonical_merge_runs
            ORDER BY id DESC LIMIT 1
            """
        )
    ).scalar_one()
    print("fk_remap summary:", fk)


def step_entity_summary(session) -> None:
    header("3", "Entity summary (AFTER apply)")
    total = session.execute(text("SELECT COUNT(*) FROM companies")).scalar_one()
    print(f"total_companies: {total}")
    for row in session.execute(
        text(
            """
            SELECT COALESCE(entity_role,'unset') AS r, COUNT(*) AS n
            FROM companies GROUP BY 1 ORDER BY n DESC
            """
        )
    ).all():
        print(f"  {row.r}: {row.n}")
    canonical_n = session.execute(
        text("SELECT COUNT(*) FROM companies WHERE entity_role = 'canonical'")
    ).scalar_one()
    print(f"canonical rows: {canonical_n}")
    print(f"permits.company_id filled: {session.execute(text('SELECT COUNT(*) FROM permits WHERE company_id IS NOT NULL')).scalar_one()}")


def step_ledcor(session) -> None:
    header("4", "Ledcor ILIKE '%ledcor%'")
    rows = session.execute(
        text(
            """
            SELECT id, name, entity_role, canonical_company_id, display_name,
                   total_projects, total_value, total_award_value, award_count
            FROM companies WHERE name ILIKE '%ledcor%'
            ORDER BY entity_role, total_value DESC NULLS LAST
            """
        )
    ).all()
    print(f"rows: {len(rows)}")
    canonical_ids: set[int] = set()
    alias_count = 0
    for row in rows:
        d = dict(row._mapping)
        role = d["entity_role"]
        if role == "canonical":
            canonical_ids.add(int(d["id"]))
        elif role == "applicant_alias" and d["canonical_company_id"]:
            canonical_ids.add(int(d["canonical_company_id"]))
            alias_count += 1
        print(
            f"  id={d['id']} role={role} fk={d['canonical_company_id']} "
            f"display={d['display_name']!r} | {d['name'][:75]}"
        )
    print(f"\nalias rows in result: {alias_count}")
    print(f"canonical ids involved: {sorted(canonical_ids)}")
    for cid in sorted(canonical_ids):
        p = session.execute(text("SELECT COUNT(*) FROM permits WHERE company_id=:id"), {"id": cid}).scalar_one()
        a = session.execute(
            text("SELECT COUNT(*), COALESCE(SUM(award_value),0) FROM contract_awards WHERE company_id=:id"),
            {"id": cid},
        ).first()
        print(f"  canonical id={cid}: permits={p}, awards={a[0]} value={a[1]}")


def step_pontem(session) -> None:
    header("5", "Pontem id=8638")
    row = session.execute(
        text(
            """
            SELECT id, name, entity_role, canonical_company_id, display_name
            FROM companies WHERE id = 8638
            """
        )
    ).first()
    print("8638:", dict(row._mapping))
    aliases = session.execute(
        text(
            """
            SELECT id, name, entity_role FROM companies
            WHERE canonical_company_id = 8638 AND entity_role = 'applicant_alias'
            ORDER BY id
            """
        )
    ).all()
    print(f"aliases -> 8638: {len(aliases)}")
    for a in aliases:
        print(f"  id={a.id} {a.name[:70]}")
    permits = session.execute(
        text("SELECT COUNT(*) FROM permits WHERE company_id = 8638")
    ).scalar_one()
    print(f"permits on canonical 8638: {permits}")


def step_fk_sample(session) -> None:
    header("6", "FK remap sample — Pontem group (8638)")
    alias_ids = session.execute(
        text(
            """
            SELECT alias_company_id FROM company_applicant_aliases
            WHERE canonical_company_id = 8638 ORDER BY alias_company_id
            """
        )
    ).scalars().all()
    print(f"registered aliases: {list(alias_ids)}")
    tables = ["contract_awards", "tender_outcomes", "client_profiles", "company_wiki"]
    for table in tables:
        for aid in alias_ids:
            rem = session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE company_id = :id"), {"id": aid}
            ).scalar_one()
            if rem:
                print(f"  STILL ON ALIAS {table} id={aid}: {rem} rows")
        on_canon = session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE company_id = 8638")
        ).scalar_one()
        print(f"  {table} on canonical 8638: {on_canon}")


def step_faucet(session) -> None:
    header("7", "Faucet: resolve + permit attach 'New Person DBA: Ledcor'")
    before = session.execute(text("SELECT COUNT(*) FROM companies")).scalar_one()
    resolver = CompanyResolver(session)
    resolution = resolver.resolve(
        "New Person DBA: Ledcor",
        source="permits:verification_test",
        city="Vancouver",
        create_if_missing=True,
    )
    after = session.execute(text("SELECT COUNT(*) FROM companies")).scalar_one()
    print(f"resolve: status={resolution.status} company_id={resolution.company_id} created={resolution.created}")
    print(f"display_name={resolution.display_name!r} method={resolution.method}")
    print(f"companies before={before} after={after} delta={after-before}")
    if resolution.company_id:
        co = session.get(Company, resolution.company_id)
        print(f"resolved -> id={co.id} name={co.name!r} role={co.entity_role} display={co.display_name!r}")

    from db.permit_import import _attach_company_ids

    test_row = {
        "external_id": "verify-ledcor-faucet-001",
        "source": "verification_test",
        "city": "Vancouver",
        "applicant": "New Person DBA: Ledcor",
        "address": "123 Test St",
        "permit_type": "New Construction",
        "project_value": "1000",
        "issue_date": "2026-01-01",
    }
    _attach_company_ids(session, [test_row], source="verification_test")
    print(f"permit row company_id after attach: {test_row.get('company_id')}")
    print(f"permit merge method: {test_row.get('canonical_merge_method')}")
    session.rollback()
    print("(session rolled back — no persistent faucet test changes)")


def main() -> None:
    guard_readonly_db(_SCRIPT)
    init_db(raise_on_failure=False)
    session = get_session()
    try:
        step_migrations(session)
        step_apply_status(session)
        step_entity_summary(session)
        step_ledcor(session)
        step_pontem(session)
        step_fk_sample(session)
        step_faucet(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
