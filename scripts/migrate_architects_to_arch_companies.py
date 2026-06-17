"""One-time migration: copy qualified Architect rows from companies → arch_companies.

Source: companies WHERE company_type = 'Architect' AND total_projects >= 3
Target: arch_companies (insert only — skip when name already exists, case-insensitive
or normalized-name match).

Field mapping (companies → arch_companies):
  name, google_address/primary_address → google_address, google_phone → google_phone
  total_projects, total_value, avg_project_value, neighborhoods
  primary_city → website_service_areas (arch_companies has no primary_city column)
  google_rating, google_reviews_count, ai_summary, ai_reliability_score
  company_type 'Architect' → primary_trade 'architecture'
  data_sources ['companies_migration'] → trade_tags includes 'companies_migration'

Usage:
  python scripts/migrate_architects_to_arch_companies.py           # dry-run (default)
  python scripts/migrate_architects_to_arch_companies.py --commit # apply inserts
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from db.connection import session_scope
from db.models import ArchCompany, Company
from pipeline.company_matching import normalize_vendor_name

MIGRATION_SOURCE_TAG = "companies_migration"
MIN_TOTAL_PROJECTS = 3
ARCHITECT_COMPANY_TYPE = "Architect"
ARCH_PRIMARY_TRADE = "architecture"


def _existing_arch_keys(session) -> tuple[set[str], set[str]]:
    """Return (lowered exact names, normalized name keys) for arch_companies."""
    lowered: set[str] = set()
    normalized: set[str] = set()
    for name, in session.execute(select(ArchCompany.name)).all():
        if not name:
            continue
        lowered.add(name.strip().lower())
        norm = normalize_vendor_name(name)
        if norm:
            normalized.add(norm)
    return lowered, normalized


def _arch_name_exists(name: str, lowered: set[str], normalized: set[str]) -> bool:
    clean = (name or "").strip()
    if not clean:
        return True
    if clean.lower() in lowered:
        return True
    norm = normalize_vendor_name(clean)
    return bool(norm and norm in normalized)


def _pick_address(company: Company) -> str:
    return (company.primary_address or company.google_address or "")[:500]


def _build_arch_payload(company: Company) -> dict:
    trade_tags = [ARCH_PRIMARY_TRADE]
    if MIGRATION_SOURCE_TAG not in trade_tags:
        trade_tags.append(MIGRATION_SOURCE_TAG)

    website_service_areas: list[str] = []
    if company.primary_city and company.primary_city.strip():
        website_service_areas = [company.primary_city.strip()[:100]]

    return {
        "name": company.name[:300],
        "total_projects": int(company.total_projects or 0),
        "total_value": float(company.total_value or 0.0),
        "avg_project_value": float(company.avg_project_value or 0.0),
        "neighborhoods": list(company.neighborhoods or [])[:15],
        "google_address": _pick_address(company),
        "google_phone": (company.google_phone or "")[:50],
        "google_rating": company.google_rating,
        "google_reviews_count": company.google_reviews_count,
        "ai_summary": company.ai_summary or "",
        "ai_reliability_score": company.ai_reliability_score,
        "primary_trade": ARCH_PRIMARY_TRADE,
        "trade_tags": trade_tags,
        "website_service_areas": website_service_areas,
    }


def migrate(*, commit: bool) -> dict[str, int | list[str]]:
    lowered: set[str] = set()
    normalized: set[str] = set()
    candidates: list[Company] = []
    skipped_existing = 0
    skipped_other = 0
    to_insert: list[dict] = []
    skipped_names: list[str] = []
    insert_names: list[str] = []

    with session_scope() as session:
        lowered, normalized = _existing_arch_keys(session)
        candidates = session.scalars(
            select(Company)
            .where(
                Company.company_type == ARCHITECT_COMPANY_TYPE,
                Company.total_projects >= MIN_TOTAL_PROJECTS,
            )
            .order_by(Company.total_projects.desc(), Company.name)
        ).all()

        for company in candidates:
            if _arch_name_exists(company.name, lowered, normalized):
                skipped_existing += 1
                skipped_names.append(company.name)
                continue

            payload = _build_arch_payload(company)
            if not payload["name"]:
                skipped_other += 1
                continue

            to_insert.append(payload)
            insert_names.append(payload["name"])
            lowered.add(payload["name"].lower())
            norm = normalize_vendor_name(payload["name"])
            if norm:
                normalized.add(norm)

        inserted = 0
        if commit and to_insert:
            table = ArchCompany.__table__
            stmt = insert(table).values(to_insert).on_conflict_do_nothing(index_elements=["name"])
            result = session.execute(stmt)
            session.commit()
            inserted = result.rowcount or 0

    return {
        "candidates": len(candidates),
        "would_insert": len(to_insert),
        "inserted": inserted if commit else 0,
        "skipped_existing": skipped_existing,
        "skipped_other": skipped_other,
        "insert_names": insert_names,
        "skipped_names": skipped_names,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply inserts. Default is dry-run (no writes).",
    )
    args = parser.parse_args()
    dry_run = not args.commit

    print(f"Mode: {'DRY-RUN (no writes)' if dry_run else 'COMMIT (inserting rows)'}")
    print(
        f"Source filter: company_type={ARCHITECT_COMPANY_TYPE!r}, "
        f"total_projects>={MIN_TOTAL_PROJECTS}"
    )

    stats = migrate(commit=not dry_run)

    print()
    print("Summary")
    print(f"  Candidates scanned:     {stats['candidates']}")
    print(f"  Would insert:           {stats['would_insert']}")
    if not dry_run:
        print(f"  Inserted:               {stats['inserted']}")
    print(f"  Skipped (exists):       {stats['skipped_existing']}")
    print(f"  Skipped (other):        {stats['skipped_other']}")

    insert_names = stats["insert_names"]
    if insert_names:
        print()
        print(f"Would insert ({len(insert_names)}):")
        for name in insert_names[:25]:
            print(f"  + {name}")
        if len(insert_names) > 25:
            print(f"  ... and {len(insert_names) - 25} more")

    skipped_names = stats["skipped_names"]
    if skipped_names:
        print()
        print(f"Skipped — already in arch_companies ({len(skipped_names)}):")
        for name in skipped_names[:15]:
            print(f"  - {name}")
        if len(skipped_names) > 15:
            print(f"  ... and {len(skipped_names) - 15} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
