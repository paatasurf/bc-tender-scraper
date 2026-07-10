"""Prototype impact analysis for canonical entity validation (read-only).

This script quantifies the effect of three potential architecture changes
without modifying any production data:

  A. Validation gate before canonical promotion: generic-bucket or
     probable-person rows would not become/remain canonical.
  B. New entity roles: explicit `generic_bucket` and `verified_company` states.
  C. CI confidence gating: exclude low-confidence / generic-bucket competitors.

Run against a production snapshot or read-only replica:

    python -m scripts.prototype_canonical_impact

The script never commits. It only reads from the database and prints a JSON
report to stdout. All classification changes are simulated in memory.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.company_analytics import company_analytics_entity_filter
from db.company_canonical_constants import (
    COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES,
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_PROBABLE_PERSON,
    ENTITY_ROLE_STANDALONE,
)
from db.connection import get_session
from db.models import ArchCompany, Company
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.parsed_identity_canonical_merge import is_generic_bucket_company_name


SAMPLE_COMPANY_IDS_FOR_CI = [
    549130,  # EllisDon Corporation
    3448,    # Chernoff Thompson Architects
    8638,    # Pontem Group
    8220,    # BC Event Management Inc.
    548832,  # GBS Construction Managers Inc.
    371,     # MGBA Inc.
    548653,  # Haeccity Studio Architecture
    10136,   # Kern BSG Management Ltd.
]


@dataclass
class ClassificationImpact:
    current_role: str
    proposed_role: str
    count: int
    sample_ids: list[int] = field(default_factory=list)


@dataclass
class CompetitorImpact:
    subject_id: int
    subject_name: str
    total_competitors: int
    removed_generic_bucket: int
    removed_probable_person: int
    removed_low_confidence: int
    removed_ids: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImpactReport:
    entity_role_distribution: dict[str, int] = field(default_factory=dict)
    classification_changes: list[ClassificationImpact] = field(default_factory=list)
    generic_bucket_canonical: list[dict[str, Any]] = field(default_factory=list)
    probable_person_standalone: list[dict[str, Any]] = field(default_factory=list)
    ci_impact: list[CompetitorImpact] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _as_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _as_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_as_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


def _company_summary(row: Company | ArchCompany) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.name,
        "display_name": getattr(row, "display_name", None),
        "entity_role": getattr(row, "entity_role", None),
        "canonical_merge_method": getattr(row, "canonical_merge_method", None),
        "total_projects": int(getattr(row, "total_projects", 0) or 0),
        "total_value": float(getattr(row, "total_value", 0) or 0),
        "award_count": int(getattr(row, "award_count", 0) or 0),
    }


def _propose_role(row: Company | ArchCompany) -> str:
    """Simulate new entity role assignment based on existing classifiers.

    Rules (prototype only):
      - canonical + generic bucket  -> generic_bucket
      - standalone + probable person -> probable_person (unchanged)
      - canonical with external-ID marker (scraper only) -> verified_company
      - canonical otherwise         -> canonical
      - applicant_alias             -> applicant_alias
      - standalone otherwise        -> standalone
    """
    role = getattr(row, "entity_role", "") or ""
    name = (getattr(row, "name", "") or "") or (getattr(row, "display_name", "") or "")

    if role == ENTITY_ROLE_CANONICAL and is_generic_bucket_company_name(name):
        return "generic_bucket"

    # Note: verified_company detection is simplified here. A real implementation
    # would check for BC Registry, Business Number, or other external IDs.
    if role == ENTITY_ROLE_CANONICAL and getattr(row, "bc_registry_number", None):
        return "verified_company"

    return role


def _fetch_cohort_members(
    session: Session,
    subject: Company,
    *,
    kind: str = "construction",
) -> list[Company | ArchCompany]:
    """Fetch the same peer pool the CI cohort SQL would see (read-only)."""
    model = ArchCompany if kind == "architecture" else Company
    query = select(model).where(model.id != subject.id)

    # Apply the same analytics entity filter used in production CI
    if kind == "construction":
        query = query.where(company_analytics_entity_filter())

    # Quality gate
    query = query.where(
        (model.total_projects >= 2) | (model.award_count >= 1)
    )

    # Sector/trade gate (when subject has them)
    sector = (subject.dominant_sector or "").strip() if hasattr(subject, "dominant_sector") else ""
    trade = (subject.primary_trade or "").strip() if hasattr(subject, "primary_trade") else ""
    if sector:
        query = query.where(
            (model.dominant_sector == sector) | (model.primary_trade == trade)
        )

    return list(session.scalars(query.limit(500)).all())


def _simulate_ci_filtering(
    members: list[Company | ArchCompany],
    subject: Company,
) -> CompetitorImpact:
    """Simulate removing generic-bucket and probable-person competitors."""
    removed: list[dict[str, Any]] = []
    generic_bucket_count = 0
    probable_person_count = 0
    low_confidence_count = 0

    for member in members:
        name = getattr(member, "name", "") or ""
        role = getattr(member, "entity_role", "") or ""
        reason: str | None = None

        if role == ENTITY_ROLE_CANONICAL and is_generic_bucket_company_name(name):
            reason = "generic_bucket_canonical"
            generic_bucket_count += 1
        elif role == ENTITY_ROLE_STANDALONE and is_probable_person_name(name):
            reason = "probable_person_standalone"
            probable_person_count += 1
        elif role in COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES:
            reason = "already_excluded_role"

        if reason:
            removed.append({
                "id": int(member.id),
                "name": name,
                "entity_role": role,
                "reason": reason,
            })

    return CompetitorImpact(
        subject_id=int(subject.id),
        subject_name=subject.name or "",
        total_competitors=len(members),
        removed_generic_bucket=generic_bucket_count,
        removed_probable_person=probable_person_count,
        removed_low_confidence=low_confidence_count,
        removed_ids=removed[:20],  # cap sample
    )


def analyze(session: Session) -> ImpactReport:
    report = ImpactReport()

    # ------------------------------------------------------------------
    # 1. Entity role distribution
    # ------------------------------------------------------------------
    role_counts: Counter[str] = Counter()
    for role, count in session.execute(
        select(Company.entity_role, func.count(Company.id)).group_by(Company.entity_role)
    ).all():
        role_counts[str(role or "unknown")] = int(count)
    report.entity_role_distribution = dict(role_counts)

    # ------------------------------------------------------------------
    # 2. Simulate new role assignments for canonical rows
    # ------------------------------------------------------------------
    change_counter: dict[tuple[str, str], list[int]] = defaultdict(list)
    generic_bucket_rows: list[dict[str, Any]] = []
    probable_person_rows: list[dict[str, Any]] = []

    # Stream companies to avoid loading all rows into memory at once
    cursor = session.execute(select(Company)).yield_per(1000)
    for row in cursor:
        current = row.entity_role or ""
        proposed = _propose_role(row)
        if current != proposed:
            change_counter[(current, proposed)].append(int(row.id))

        if current == ENTITY_ROLE_CANONICAL and is_generic_bucket_company_name(row.name or ""):
            generic_bucket_rows.append(_company_summary(row))
        if current == ENTITY_ROLE_STANDALONE and is_probable_person_name(row.name or ""):
            probable_person_rows.append(_company_summary(row))

    report.classification_changes = [
        ClassificationImpact(
            current_role=current,
            proposed_role=proposed,
            count=len(ids),
            sample_ids=ids[:10],
        )
        for (current, proposed), ids in sorted(change_counter.items())
    ]
    report.generic_bucket_canonical = generic_bucket_rows[:50]
    report.probable_person_standalone = probable_person_rows[:50]

    # ------------------------------------------------------------------
    # 3. Simulate CI impact for a sample of canonical companies
    # ------------------------------------------------------------------
    for company_id in SAMPLE_COMPANY_IDS_FOR_CI:
        subject = session.get(Company, company_id)
        if subject is None:
            continue
        members = _fetch_cohort_members(session, subject, kind="construction")
        report.ci_impact.append(_simulate_ci_filtering(members, subject))

    # ------------------------------------------------------------------
    # 4. Summary
    # ------------------------------------------------------------------
    total_canonical = role_counts.get(ENTITY_ROLE_CANONICAL, 0)
    report.summary = {
        "total_companies": sum(role_counts.values()),
        "total_canonical": total_canonical,
        "generic_bucket_canonical_count": len(generic_bucket_rows),
        "probable_person_standalone_count": len(probable_person_rows),
        "role_change_count": sum(len(ids) for ids in change_counter.values()),
        "ci_sample_subjects": len(report.ci_impact),
        "note": "All changes are simulated; no database writes occurred.",
    }

    return report


def main() -> None:
    session = get_session()
    try:
        report = analyze(session)
        print(json.dumps(_as_dict(report), indent=2, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
