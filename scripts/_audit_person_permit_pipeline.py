"""Read-only audit: person names as competitors vs missing companies in permit pipeline."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import config.env  # noqa: F401

from sqlalchemy import or_, select

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company, Permit
from pipeline.company_matching import normalize_vendor_name
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.company_resolution import CompanyResolver

TARGETS = [
    ("Akash Sidhu", 220),
    ("Naki Ocran", 6560),
    ("Kevin To", 8308),
    ("Tijana Sljivic", 10928),
    ("Shalindro Dosanjh", 1152),
]

# Company-like patterns often buried in Vancouver description text
DESC_COMPANY_PATTERNS = [
    re.compile(r"\bHPO:\s*([^.\n\r]+)", re.I),
    re.compile(r"\bHomeowner Protection Office:\s*([^.\n\r]+)", re.I),
    re.compile(r"\bResidential Builder\s*[-–:]\s*([^.\n\r]+)", re.I),
    re.compile(r"\bDemo(?:lition)? Contractor:\s*([^.\n\r]+)", re.I),
    re.compile(r"\bBuilding Contractor:\s*([^.\n\r]+)", re.I),
    re.compile(r"\bContractor:\s*([^.\n\r]+)", re.I),
    re.compile(r"\b(?:General Contractor|GC):\s*([^.\n\r]+)", re.I),
]


def extract_companies_from_description(description: str) -> list[str]:
    found: list[str] = []
    for pattern in DESC_COMPANY_PATTERNS:
        for match in pattern.findall(description or ""):
            cleaned = match.strip(" .")
            if cleaned and len(cleaned) > 3:
                found.append(cleaned)
    return found


def lookup_csv_row(external_id: str, source: str) -> dict[str, str] | None:
    import csv
    from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


    path = Path("building_permits.csv")
    if not path.is_file():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("external_id") or "") == external_id and (row.get("source") or "vancouver") == source:
                return row
    return None


def simulate_resolution(raw: str, *, city: str, source: str, session) -> dict:
    resolver = CompanyResolver(session)
    resolution = resolver.resolve(raw, source=source, city=city, create_if_missing=False)
    return {
        "raw": raw,
        "status": resolution.status,
        "method": resolution.method,
        "company_id": resolution.company_id,
        "probable_person": is_probable_person_name(raw),
    }


def audit_person(name: str, company_id: int, session) -> dict:
    company = session.get(Company, company_id)
    permits = session.scalars(
        select(Permit)
        .where(
            or_(
                Permit.company_id == company_id,
                Permit.applicant.ilike(f"%{name}%"),
                Permit.contractor.ilike(f"%{name}%"),
            )
        )
        .order_by(Permit.issue_date.desc())
    ).all()

    linked = [p for p in permits if p.company_id == company_id]
    name_matches = [p for p in permits if p not in linked]

    permit_reports = []
    contractor_names: Counter[str] = Counter()
    desc_companies: Counter[str] = Counter()

    for permit in linked[:25]:
        csv_row = lookup_csv_row(permit.external_id, permit.source)
        desc_hits = extract_companies_from_description(permit.description)
        for hit in desc_hits:
            desc_companies[hit] += 1
        if permit.contractor:
            contractor_names[permit.contractor.strip()] += 1

        applicant_resolution = simulate_resolution(
            permit.applicant or "",
            city=permit.city or "",
            source=f"permits:{permit.source}",
            session=session,
        )
        contractor_resolution = (
            simulate_resolution(
                permit.contractor or "",
                city=permit.city or "",
                source=f"permits:{permit.source}",
                session=session,
            )
            if (permit.contractor or "").strip()
            else None
        )

        permit_reports.append(
            {
                "permit_id": permit.id,
                "external_id": permit.external_id,
                "source": permit.source,
                "issue_date": permit.issue_date,
                "db_applicant": permit.applicant,
                "db_contractor": permit.contractor,
                "db_company_id": permit.company_id,
                "csv_applicant": (csv_row or {}).get("applicant"),
                "csv_contractor": (csv_row or {}).get("contractor"),
                "description_company_mentions": desc_hits[:5],
                "applicant_resolution": applicant_resolution,
                "contractor_resolution": contractor_resolution,
                "import_would_use": (permit.applicant or permit.contractor or "").strip(),
            }
        )

    return {
        "person": name,
        "company_id": company_id,
        "company_row": {
            "name": company.name if company else None,
            "display_name": company.display_name if company else None,
            "entity_role": company.entity_role if company else None,
            "canonical_vendor_name": company.canonical_vendor_name if company else None,
            "total_projects": company.total_projects if company else None,
            "canonical_merge_method": company.canonical_merge_method if company else None,
        },
        "permit_counts": {
            "linked_to_company_id": len(linked),
            "name_match_not_linked": len(name_matches),
        },
        "top_contractors_on_linked_permits": contractor_names.most_common(10),
        "top_description_company_mentions": desc_companies.most_common(10),
        "sample_permits": permit_reports,
    }


def main() -> None:
    guard_readonly_db(_SCRIPT)
    session = get_session()
    try:
        results = [audit_person(name, cid, session) for name, cid in TARGETS]
        print(json.dumps(results, indent=2, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
