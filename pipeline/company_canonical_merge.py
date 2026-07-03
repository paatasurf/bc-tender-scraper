"""Deterministic canonical company merge from permit applicant names.

Groups duplicate applicant-based company rows under one canonical company name
(extracted from DBA or legal name). Never deletes permits or company rows.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    CONFIDENCE_DBA_EXPLICIT,
    CONFIDENCE_LEGAL_ONLY,
    CONFIDENCE_NORMALIZED_KEY,
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_PROBABLE_PERSON,
    ENTITY_ROLE_STANDALONE,
    FORCED_CANONICAL_IDS_BY_KEY,
    MERGE_METHOD_DBA_APPLICANT,
    MERGE_METHOD_DBA_NAME,
    MERGE_METHOD_EXACT_APPLICANT,
    MERGE_METHOD_LEGAL_APPLICANT,
    MERGE_METHOD_NORMALIZED_KEY,
    MERGE_METHOD_CONTRACTOR,
    MERGE_RUN_STATUS_APPLIED,
    MERGE_RUN_STATUS_PLANNED,
    MERGE_RUN_STATUS_ROLLED_BACK,
    MERGE_TIER_EXCLUDED_PROBABLE_PERSON,
    MERGE_TIER_EXCLUDED_REVIEW,
    MERGE_TIER_SAFE_DBA,
)
from db.models import (
    Company,
    CompanyApplicantAlias,
    CompanyCanonicalMergeRollback,
    CompanyCanonicalMergeRun,
    Permit,
)
from pipeline.company_classification import parse_name
from pipeline.company_fk_remap import remap_company_foreign_keys
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.company_matching import normalize_vendor_name

MAX_NAME_LEN = 300
MAX_SAMPLE_GROUPS = 50

def classify_merge_group(group: MergeGroup) -> str:
    """Partition merge groups into safe DBA vs excluded non-DBA buckets."""
    if any(member.has_dba for member in group.members):
        return MERGE_TIER_SAFE_DBA
    if is_probable_person_name(group.display_name):
        return MERGE_TIER_EXCLUDED_PROBABLE_PERSON
    return MERGE_TIER_EXCLUDED_REVIEW


def _apply_forced_canonical(group: MergeGroup) -> None:
    forced_id = FORCED_CANONICAL_IDS_BY_KEY.get(group.canonical_key)
    if forced_id is None:
        return
    member_ids = {member.company_id for member in group.members}
    if forced_id not in member_ids:
        return
    group.primary_company_id = forced_id
    group.create_canonical_row = False
    group.canonical_name_for_insert = ""


@dataclass
class CompanyRecord:
    id: int
    name: str
    total_projects: int = 0
    total_value: float = 0.0
    total_award_value: float = 0.0
    award_count: int = 0


@dataclass
class ResolvedCompanyName:
    raw_name: str
    display_name: str
    signatory: str
    canonical_key: str
    has_dba: bool
    confidence: float
    method: str


@dataclass
class MergeGroupMember:
    company_id: int
    name: str
    signatory: str
    has_dba: bool
    total_projects: int
    total_value: float
    total_award_value: float
    award_count: int = 0
    confidence: float = 1.0
    method: str = ""


@dataclass
class MergeGroup:
    canonical_key: str
    display_name: str
    confidence: float
    method: str
    members: list[MergeGroupMember] = field(default_factory=list)
    primary_company_id: int | None = None
    create_canonical_row: bool = False
    canonical_name_for_insert: str = ""


@dataclass
class PermitAssignment:
    permit_id: int
    applicant_raw: str
    contractor_raw: str
    canonical_company_id: int
    confidence: float
    method: str
    previous_company_id: int | None = None


@dataclass
class MergePlan:
    generated_at: str
    groups: list[MergeGroup]
    standalone_company_ids: list[int]
    permit_assignments: list[PermitAssignment]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_report_dict(self) -> dict[str, Any]:
        merge_groups = [group for group in self.groups if len(group.members) > 1]
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "merge_groups": [
                {
                    "canonical_key": group.canonical_key,
                    "display_name": group.display_name,
                    "confidence": group.confidence,
                    "method": group.method,
                    "primary_company_id": group.primary_company_id,
                    "create_canonical_row": group.create_canonical_row,
                    "canonical_name_for_insert": group.canonical_name_for_insert,
                    "member_count": len(group.members),
                    "members": [asdict(member) for member in group.members],
                }
                for group in merge_groups[:MAX_SAMPLE_GROUPS]
            ],
            "merge_groups_truncated": max(0, len(merge_groups) - MAX_SAMPLE_GROUPS),
            "standalone_company_count": len(self.standalone_company_ids),
            "permit_assignment_samples": [
                asdict(item)
                for item in self.permit_assignments[:MAX_SAMPLE_GROUPS]
            ],
            "permit_assignments_truncated": max(0, len(self.permit_assignments) - MAX_SAMPLE_GROUPS),
        }


def _clamp_name(value: str) -> str:
    return (value or "").strip()[:MAX_NAME_LEN]


def resolve_company_name(raw_name: str) -> ResolvedCompanyName | None:
    """Deterministically derive canonical display name from a company/applicant string."""
    cleaned = _clamp_name(raw_name)
    if not cleaned:
        return None

    parsed = parse_name(cleaned)
    display = _clamp_name(parsed["dba"] or parsed["legal"])
    if not display:
        return None

    key = normalize_vendor_name(display)
    if not key:
        return None

    if parsed["has_dba"]:
        return ResolvedCompanyName(
            raw_name=cleaned,
            display_name=display,
            signatory=_clamp_name(parsed["legal"]) if parsed["dba"] else "",
            canonical_key=key,
            has_dba=True,
            confidence=CONFIDENCE_DBA_EXPLICIT,
            method=MERGE_METHOD_DBA_NAME,
        )

    return ResolvedCompanyName(
        raw_name=cleaned,
        display_name=display,
        signatory="",
        canonical_key=key,
        has_dba=False,
        confidence=CONFIDENCE_NORMALIZED_KEY,
        method=MERGE_METHOD_NORMALIZED_KEY,
    )


def _pick_display_name(members: list[MergeGroupMember]) -> str:
    dba_names = Counter(
        resolve_company_name(member.name).display_name
        for member in members
        if resolve_company_name(member.name) and resolve_company_name(member.name).has_dba
    )
    if dba_names:
        return dba_names.most_common(1)[0][0]

    legal_names = Counter(
        resolve_company_name(member.name).display_name
        for member in members
        if resolve_company_name(member.name)
    )
    return legal_names.most_common(1)[0][0] if legal_names else members[0].name


def _member_score(member: MergeGroupMember) -> tuple[float, int, int]:
    return (
        member.total_value + member.total_award_value,
        member.total_projects + member.award_count,
        -member.company_id,
    )


def _choose_primary(
    members: list[MergeGroupMember],
    display_name: str,
) -> tuple[int, bool, str]:
    """Return (primary_id, create_new_row, insert_name)."""
    target = display_name.casefold()
    for member in members:
        if member.name.casefold() == target:
            return member.company_id, False, ""

    for member in members:
        resolved = resolve_company_name(member.name)
        if resolved and not resolved.has_dba and resolved.display_name.casefold() == target:
            return member.company_id, False, ""

    ranked = sorted(members, key=_member_score, reverse=True)
    primary = ranked[0]
    if primary.name.casefold() == display_name.casefold():
        return primary.company_id, False, ""

    return primary.company_id, True, display_name


def _load_companies(session: Session) -> list[CompanyRecord]:
    rows = session.execute(
        select(
            Company.id,
            Company.name,
            Company.total_projects,
            Company.total_value,
            Company.total_award_value,
            Company.award_count,
        )
    ).all()
    return [
        CompanyRecord(
            id=int(row.id),
            name=str(row.name or ""),
            total_projects=int(row.total_projects or 0),
            total_value=float(row.total_value or 0.0),
            total_award_value=float(row.total_award_value or 0.0),
            award_count=int(row.award_count or 0),
        )
        for row in rows
    ]


def build_merge_plan(session: Session) -> MergePlan:
    """Build a deterministic merge plan without mutating data."""
    companies = _load_companies(session)
    grouped: dict[str, list[MergeGroupMember]] = defaultdict(list)
    unresolved: list[int] = []

    for company in companies:
        resolved = resolve_company_name(company.name)
        if resolved is None:
            unresolved.append(company.id)
            continue
        grouped[resolved.canonical_key].append(
            MergeGroupMember(
                company_id=company.id,
                name=company.name,
                signatory=resolved.signatory,
                has_dba=resolved.has_dba,
                total_projects=company.total_projects,
                total_value=company.total_value,
                total_award_value=company.total_award_value,
                award_count=company.award_count,
                confidence=resolved.confidence,
                method=resolved.method,
            )
        )

    groups: list[MergeGroup] = []
    standalone_ids: list[int] = list(unresolved)

    for key, members in grouped.items():
        if len(members) == 1:
            standalone_ids.append(members[0].company_id)
            continue

        display_name = _pick_display_name(members)
        primary_id, create_row, insert_name = _choose_primary(members, display_name)
        group_confidence = max(member.confidence for member in members)
        group_method = MERGE_METHOD_DBA_NAME if any(member.has_dba for member in members) else MERGE_METHOD_NORMALIZED_KEY

        groups.append(
            MergeGroup(
                canonical_key=key,
                display_name=display_name,
                confidence=group_confidence,
                method=group_method,
                members=members,
                primary_company_id=primary_id,
                create_canonical_row=create_row,
                canonical_name_for_insert=insert_name,
            )
        )
        _apply_forced_canonical(groups[-1])

    key_to_canonical = _canonical_lookup(groups, standalone_ids, companies)
    permit_assignments = _plan_permit_assignments(session, key_to_canonical, companies)

    merge_groups = [group for group in groups if len(group.members) > 1]
    safe_groups, excluded_person, excluded_review = partition_merge_groups(merge_groups)
    safe_permit_ids = _safe_permit_assignment_ids(permit_assignments, safe_groups)

    alias_count = sum(len(group.members) - 1 for group in merge_groups)
    safe_alias_count = sum(len(group.members) - 1 for group in safe_groups)
    create_count = sum(1 for group in merge_groups if group.create_canonical_row)
    safe_create_count = sum(1 for group in safe_groups if group.create_canonical_row)
    reassigned = len(permit_assignments)
    safe_reassigned = len(safe_permit_ids)
    if _permits_have_company_id(session):
        already_linked = session.execute(
            select(func.count()).select_from(Permit).where(Permit.company_id.isnot(None))
        ).scalar_one()
    else:
        already_linked = 0

    summary = {
        "total_companies": len(companies),
        "unresolved_companies": len(unresolved),
        "merge_groups": len(merge_groups),
        "companies_in_merge_groups": sum(len(group.members) for group in merge_groups),
        "companies_to_mark_alias": alias_count,
        "canonical_rows_to_create": create_count,
        "standalone_companies": len(standalone_ids),
        "permits_to_assign": reassigned,
        "permits_already_linked": int(already_linked),
        "confidence_breakdown": _confidence_breakdown(merge_groups, permit_assignments),
        "safe_auto_merge": {
            "merge_groups": len(safe_groups),
            "companies_in_groups": sum(len(group.members) for group in safe_groups),
            "companies_to_mark_alias": safe_alias_count,
            "canonical_rows_to_create": safe_create_count,
            "permits_to_assign": safe_reassigned,
        },
        "excluded_from_apply": {
            "probable_person_groups": len(excluded_person),
            "probable_person_companies": sum(len(g.members) for g in excluded_person),
            "review_queue_groups": len(excluded_review),
            "review_queue_companies": sum(len(g.members) for g in excluded_review),
            "non_dba_groups_total": len(excluded_person) + len(excluded_review),
        },
    }

    return MergePlan(
        generated_at=datetime.now(timezone.utc).isoformat(),
        groups=groups,
        standalone_company_ids=standalone_ids,
        permit_assignments=permit_assignments,
        summary=summary,
    )


def partition_merge_groups(
    merge_groups: list[MergeGroup],
) -> tuple[list[MergeGroup], list[MergeGroup], list[MergeGroup]]:
    safe: list[MergeGroup] = []
    probable_person: list[MergeGroup] = []
    review: list[MergeGroup] = []
    for group in merge_groups:
        tier = classify_merge_group(group)
        if tier == MERGE_TIER_SAFE_DBA:
            safe.append(group)
        elif tier == MERGE_TIER_EXCLUDED_PROBABLE_PERSON:
            probable_person.append(group)
        else:
            review.append(group)
    return safe, probable_person, review


def _safe_permit_assignment_ids(
    assignments: list[PermitAssignment],
    safe_groups: list[MergeGroup],
) -> set[int]:
    safe_keys = {group.canonical_key for group in safe_groups}
    safe_primary_ids = {int(group.primary_company_id or 0) for group in safe_groups}
    safe_ids: set[int] = set()
    for item in assignments:
        if item.canonical_company_id in safe_primary_ids:
            safe_ids.add(item.permit_id)
            continue
        resolved = resolve_company_name(item.applicant_raw) or resolve_company_name(item.contractor_raw)
        if resolved and resolved.has_dba and resolved.canonical_key in safe_keys:
            safe_ids.add(item.permit_id)
    return safe_ids


def safe_merge_groups(plan: MergePlan) -> list[MergeGroup]:
    merge_groups = [group for group in plan.groups if len(group.members) > 1]
    safe, _, _ = partition_merge_groups(merge_groups)
    return safe


def safe_permit_assignments(plan: MergePlan) -> list[PermitAssignment]:
    safe_groups = safe_merge_groups(plan)
    safe_ids = _safe_permit_assignment_ids(plan.permit_assignments, safe_groups)
    return [item for item in plan.permit_assignments if item.permit_id in safe_ids]


def _canonical_lookup(
    groups: list[MergeGroup],
    standalone_ids: list[int],
    companies: list[CompanyRecord],
) -> dict[str, int]:
    lookup: dict[str, int] = {}
    company_by_id = {company.id: company for company in companies}

    for group in groups:
        if len(group.members) <= 1:
            continue
        lookup[group.canonical_key] = int(group.primary_company_id or 0)

    for company_id in standalone_ids:
        company = company_by_id.get(company_id)
        if company is None:
            continue
        resolved = resolve_company_name(company.name)
        if resolved is None:
            continue
        lookup.setdefault(resolved.canonical_key, company_id)

    return lookup


def _resolve_permit_party(
    raw: str,
    key_to_canonical: dict[str, int],
    exact_name_to_canonical: dict[str, int],
) -> tuple[int | None, float, str]:
    cleaned = _clamp_name(raw)
    if not cleaned:
        return None, 0.0, ""

    if cleaned in exact_name_to_canonical:
        return exact_name_to_canonical[cleaned], CONFIDENCE_DBA_EXPLICIT, MERGE_METHOD_EXACT_APPLICANT

    resolved = resolve_company_name(cleaned)
    if resolved is None:
        return None, 0.0, ""

    canonical_id = key_to_canonical.get(resolved.canonical_key)
    if canonical_id is None:
        return None, 0.0, ""

    if resolved.has_dba:
        return canonical_id, CONFIDENCE_DBA_EXPLICIT, MERGE_METHOD_DBA_APPLICANT

    return canonical_id, CONFIDENCE_LEGAL_ONLY, MERGE_METHOD_LEGAL_APPLICANT


def _permits_have_company_id(session: Session) -> bool:
    row = session.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'permits'
              AND column_name = 'company_id'
            """
        )
    ).first()
    return row is not None


def _plan_permit_assignments(
    session: Session,
    key_to_canonical: dict[str, int],
    companies: list[CompanyRecord],
) -> list[PermitAssignment]:
    exact_name_to_canonical: dict[str, int] = {}
    plan_groups = _groups_from_companies(companies)
    plan_groups = [group for group in plan_groups if len(group.members) > 1]

    for group in plan_groups:
        canonical_id = int(group.primary_company_id or 0)
        for member in group.members:
            exact_name_to_canonical[member.name] = canonical_id

    for company in companies:
        resolved = resolve_company_name(company.name)
        if resolved is None:
            continue
        canonical_id = key_to_canonical.get(resolved.canonical_key)
        if canonical_id is not None:
            exact_name_to_canonical.setdefault(company.name, canonical_id)

    assignments: list[PermitAssignment] = []
    has_company_id = _permits_have_company_id(session)
    if has_company_id:
        permit_query = select(
            Permit.id,
            Permit.applicant,
            Permit.contractor,
            Permit.company_id,
        )
    else:
        permit_query = select(
            Permit.id,
            Permit.applicant,
            Permit.contractor,
        )

    rows = session.execute(permit_query).yield_per(2000)

    for row in rows:
        if has_company_id:
            permit_id, applicant, contractor, existing_company_id = row
        else:
            permit_id, applicant, contractor = row
            existing_company_id = None
        for raw, method_prefix in (
            (applicant, "applicant"),
            (contractor, "contractor"),
        ):
            if method_prefix == "contractor" and _clamp_name(raw) == _clamp_name(applicant):
                continue

            canonical_id, confidence, method = _resolve_permit_party(
                raw,
                key_to_canonical,
                exact_name_to_canonical,
            )
            if canonical_id is None:
                continue

            if method_prefix == "contractor":
                method = MERGE_METHOD_CONTRACTOR

            assignments.append(
                PermitAssignment(
                    permit_id=int(permit_id),
                    applicant_raw=str(applicant or ""),
                    contractor_raw=str(contractor or ""),
                    canonical_company_id=canonical_id,
                    confidence=confidence,
                    method=method,
                    previous_company_id=int(existing_company_id) if existing_company_id else None,
                )
            )
            break

    # Deduplicate by permit_id — first winning match only
    deduped: dict[int, PermitAssignment] = {}
    for item in assignments:
        deduped.setdefault(item.permit_id, item)
    return list(deduped.values())


def _groups_from_companies(companies: list[CompanyRecord]) -> list[MergeGroup]:
    grouped: dict[str, list[MergeGroupMember]] = defaultdict(list)
    for company in companies:
        resolved = resolve_company_name(company.name)
        if resolved is None:
            continue
        grouped[resolved.canonical_key].append(
            MergeGroupMember(
                company_id=company.id,
                name=company.name,
                signatory=resolved.signatory,
                has_dba=resolved.has_dba,
                total_projects=company.total_projects,
                total_value=company.total_value,
                total_award_value=company.total_award_value,
                award_count=company.award_count,
                confidence=resolved.confidence,
                method=resolved.method,
            )
        )

    groups: list[MergeGroup] = []
    for key, members in grouped.items():
        if len(members) <= 1:
            continue
        display_name = _pick_display_name(members)
        primary_id, create_row, insert_name = _choose_primary(members, display_name)
        groups.append(
            MergeGroup(
                canonical_key=key,
                display_name=display_name,
                confidence=max(member.confidence for member in members),
                method=MERGE_METHOD_DBA_NAME if any(member.has_dba for member in members) else MERGE_METHOD_NORMALIZED_KEY,
                members=members,
                primary_company_id=primary_id,
                create_canonical_row=create_row,
                canonical_name_for_insert=insert_name,
            )
        )
    return groups


def _confidence_breakdown(
    merge_groups: list[MergeGroup],
    permit_assignments: list[PermitAssignment],
) -> dict[str, int]:
    breakdown: Counter[str] = Counter()
    for group in merge_groups:
        breakdown[f"company_group:{group.method}"] += 1
    for item in permit_assignments:
        label = f"permit:{item.method}:confidence_{item.confidence}"
        breakdown[label] += 1
    return dict(breakdown)


def _company_snapshot(company: Company) -> dict[str, Any]:
    return {
        "display_name": company.display_name,
        "entity_role": company.entity_role,
        "canonical_company_id": company.canonical_company_id,
        "applicant_signatory": company.applicant_signatory,
        "canonical_merge_confidence": company.canonical_merge_confidence,
        "canonical_merge_method": company.canonical_merge_method,
    }


def _permit_snapshot(permit: Permit) -> dict[str, Any]:
    return {
        "company_id": permit.company_id,
        "canonical_merge_confidence": permit.canonical_merge_confidence,
        "canonical_merge_method": permit.canonical_merge_method,
    }


def apply_merge_plan(
    session: Session,
    plan: MergePlan,
) -> CompanyCanonicalMergeRun:
    """Apply merge plan with rollback snapshots. Always commits."""
    run = CompanyCanonicalMergeRun(
        status=MERGE_RUN_STATUS_PLANNED,
        dry_run=False,
        report_json=plan.to_report_dict(),
        summary_json=plan.summary,
    )
    session.add(run)
    session.flush()

    inserted_company_ids: list[int] = []
    merge_groups = safe_merge_groups(plan)
    safe_assignments = {item.permit_id: item for item in safe_permit_assignments(plan)}

    print(f"[MergeApply] marking {len(merge_groups)} safe merge groups...")
    for group in merge_groups:
        primary_id = group.primary_company_id
        if group.create_canonical_row:
            insert_name = _clamp_name(group.canonical_name_for_insert or group.display_name)
            existing = session.execute(
                select(Company.id).where(Company.name == insert_name)
            ).scalar_one_or_none()
            if existing is not None:
                primary_id = int(existing)
            else:
                new_company = Company(
                    name=insert_name,
                    display_name=group.display_name,
                    entity_role=ENTITY_ROLE_CANONICAL,
                    canonical_merge_confidence=group.confidence,
                    canonical_merge_method=group.method,
                )
                session.add(new_company)
                session.flush()
                primary_id = int(new_company.id)
                inserted_company_ids.append(primary_id)
                session.add(
                    CompanyCanonicalMergeRollback(
                        run_id=run.id,
                        entity_type="company_insert",
                        entity_id=primary_id,
                        before_json={"inserted": True, "name": insert_name},
                    )
                )

        assert primary_id is not None
        group.primary_company_id = primary_id

        primary = session.get(Company, primary_id)
        if primary is not None:
            session.add(
                CompanyCanonicalMergeRollback(
                    run_id=run.id,
                    entity_type="company",
                    entity_id=primary_id,
                    before_json=_company_snapshot(primary),
                )
            )
            primary.display_name = group.display_name
            primary.entity_role = ENTITY_ROLE_CANONICAL
            primary.canonical_company_id = None
            primary.applicant_signatory = ""
            primary.canonical_merge_confidence = group.confidence
            primary.canonical_merge_method = group.method

        for member in group.members:
            if member.company_id == primary_id:
                continue

            alias_row = session.get(Company, member.company_id)
            if alias_row is None:
                continue

            session.add(
                CompanyCanonicalMergeRollback(
                    run_id=run.id,
                    entity_type="company",
                    entity_id=member.company_id,
                    before_json=_company_snapshot(alias_row),
                )
            )
            alias_row.entity_role = ENTITY_ROLE_APPLICANT_ALIAS
            alias_row.canonical_company_id = primary_id
            alias_row.display_name = group.display_name
            alias_row.applicant_signatory = member.signatory
            alias_row.canonical_merge_confidence = member.confidence
            alias_row.canonical_merge_method = member.method

            session.add(
                CompanyApplicantAlias(
                    canonical_company_id=primary_id,
                    alias_company_id=member.company_id,
                    applicant_name_raw=member.name,
                    signatory_name=member.signatory,
                    merge_run_id=run.id,
                    confidence=member.confidence,
                    merge_method=member.method,
                )
            )

    for company_id in plan.standalone_company_ids:
        company = session.get(Company, company_id)
        if company is None:
            continue
        resolved = resolve_company_name(company.name)
        if resolved is None:
            continue
        session.add(
            CompanyCanonicalMergeRollback(
                run_id=run.id,
                entity_type="company",
                entity_id=company_id,
                before_json=_company_snapshot(company),
            )
        )
        company.entity_role = ENTITY_ROLE_STANDALONE
        company.canonical_company_id = None
        company.display_name = resolved.display_name
        company.applicant_signatory = resolved.signatory
        company.canonical_merge_confidence = resolved.confidence
        company.canonical_merge_method = resolved.method

    print(f"[MergeApply] assigning {len(safe_assignments)} permits...")
    permit_ids = list(safe_assignments.keys())
    batch_size = 500
    for offset in range(0, len(permit_ids), batch_size):
        chunk_ids = permit_ids[offset : offset + batch_size]
        permits = session.scalars(select(Permit).where(Permit.id.in_(chunk_ids))).all()
        for permit in permits:
            assignment = safe_assignments[int(permit.id)]
            session.add(
                CompanyCanonicalMergeRollback(
                    run_id=run.id,
                    entity_type="permit",
                    entity_id=int(permit.id),
                    before_json=_permit_snapshot(permit),
                )
            )
            permit.company_id = assignment.canonical_company_id
            permit.canonical_merge_confidence = assignment.confidence
            permit.canonical_merge_method = assignment.method
        if offset and offset % 5000 == 0:
            print(f"[MergeApply] permits assigned: {offset}/{len(permit_ids)}")

    alias_to_canonical: dict[int, int] = {}
    for group in merge_groups:
        primary_id = int(group.primary_company_id or 0)
        for member in group.members:
            if member.company_id != primary_id:
                alias_to_canonical[member.company_id] = primary_id

    fk_rollback_rows: list[dict[str, Any]] = []
    print(f"[MergeApply] FK remap for {len(alias_to_canonical)} alias ids...")
    fk_summary = remap_company_foreign_keys(
        session,
        alias_to_canonical,
        rollback_store=fk_rollback_rows,
    )
    for item in fk_rollback_rows:
        session.add(
            CompanyCanonicalMergeRollback(
                run_id=run.id,
                entity_type=item["entity_type"],
                entity_id=item["entity_id"],
                before_json=item["before_json"],
            )
        )

    print(f"[MergeApply] marking probable_person companies...")
    probable_person_count = _mark_probable_person_companies(session, plan, run_id=run.id)

    run.status = MERGE_RUN_STATUS_APPLIED
    run.finished_at = datetime.now(timezone.utc)
    run.summary_json = {
        **plan.summary,
        "inserted_canonical_company_ids": inserted_company_ids,
        "run_applied": True,
        "applied_tier": MERGE_TIER_SAFE_DBA,
        "applied_merge_groups": len(merge_groups),
        "applied_permit_assignments": len(safe_assignments),
        "fk_remap": fk_summary,
        "probable_person_marked": probable_person_count,
    }
    session.commit()
    return run


def _mark_probable_person_companies(
    session: Session,
    plan: MergePlan,
    *,
    run_id: int,
) -> int:
    merge_groups = [group for group in plan.groups if len(group.members) > 1]
    _, person_groups, _ = partition_merge_groups(merge_groups)
    marked = 0
    for group in person_groups:
        for member in group.members:
            company = session.get(Company, member.company_id)
            if company is None:
                continue
            session.add(
                CompanyCanonicalMergeRollback(
                    run_id=run_id,
                    entity_type="company",
                    entity_id=member.company_id,
                    before_json=_company_snapshot(company),
                )
            )
            company.entity_role = ENTITY_ROLE_PROBABLE_PERSON
            company.canonical_company_id = None
            company.display_name = group.display_name
            company.applicant_signatory = member.signatory or member.name
            company.canonical_merge_method = "probable_person"
            marked += 1
    return marked


def rollback_merge_run(session: Session, run_id: int) -> dict[str, Any]:
    """Restore company/permit rows snapshotted during apply."""
    run = session.get(CompanyCanonicalMergeRun, run_id)
    if run is None:
        raise ValueError(f"Merge run {run_id} not found")
    if run.status == MERGE_RUN_STATUS_ROLLED_BACK:
        raise ValueError(f"Merge run {run_id} already rolled back")
    if run.dry_run:
        raise ValueError(f"Merge run {run_id} was a dry run — nothing to roll back")

    rows = session.execute(
        select(CompanyCanonicalMergeRollback).where(
            CompanyCanonicalMergeRollback.run_id == run_id
        )
    ).scalars().all()

    restored = {"company": 0, "permit": 0, "company_insert_deleted": 0, "fk": 0}

    for row in rows:
        if row.entity_type.startswith("fk:"):
            table = row.entity_type.split(":", 1)[1]
            before = row.before_json
            for col, old_value in before.items():
                session.execute(
                    text(f"UPDATE {table} SET {col} = :old_value WHERE id = :row_id"),
                    {"old_value": old_value, "row_id": row.entity_id},
                )
            restored["fk"] += 1
        elif row.entity_type == "company":
            company = session.get(Company, row.entity_id)
            if company is None:
                continue
            before = row.before_json
            company.display_name = before.get("display_name", "")
            company.entity_role = before.get("entity_role", ENTITY_ROLE_STANDALONE)
            company.canonical_company_id = before.get("canonical_company_id")
            company.applicant_signatory = before.get("applicant_signatory", "")
            company.canonical_merge_confidence = before.get("canonical_merge_confidence")
            company.canonical_merge_method = before.get("canonical_merge_method", "")
            restored["company"] += 1
        elif row.entity_type == "permit":
            permit = session.get(Permit, row.entity_id)
            if permit is None:
                continue
            before = row.before_json
            permit.company_id = before.get("company_id")
            permit.canonical_merge_confidence = before.get("canonical_merge_confidence")
            permit.canonical_merge_method = before.get("canonical_merge_method", "")
            restored["permit"] += 1
        elif row.entity_type == "company_insert":
            company = session.get(Company, row.entity_id)
            if company is not None:
                session.delete(company)
                restored["company_insert_deleted"] += 1

    session.execute(
        delete(CompanyApplicantAlias).where(CompanyApplicantAlias.merge_run_id == run_id)
    )
    run.status = MERGE_RUN_STATUS_ROLLED_BACK
    run.finished_at = datetime.now(timezone.utc)
    session.commit()

    return {"run_id": run_id, "restored": restored}


def write_merge_report(plan: MergePlan, path: str) -> None:
    payload = plan.to_report_dict()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def format_merge_report_summary(plan: MergePlan) -> str:
    lines = [
        "Company Canonical Merge Report",
        f"Generated: {plan.generated_at}",
        "",
        "Summary:",
    ]
    for key, value in plan.summary.items():
        if key == "confidence_breakdown":
            lines.append("  confidence_breakdown:")
            for label, count in value.items():
                lines.append(f"    {label}: {count}")
        elif isinstance(value, dict):
            lines.append(f"  {key}:")
            for sub_key, sub_val in value.items():
                lines.append(f"    {sub_key}: {sub_val}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)
