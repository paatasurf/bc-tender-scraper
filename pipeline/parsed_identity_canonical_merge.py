"""Scenario B: merge companies by parsed_identities applicant business_name.

Groups permit-linked parsed identities (field_name=applicant only) by
normalize_vendor_name(business_name). Reclassifies existing rows as aliases;
never inserts or deletes company rows.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    CONFIDENCE_NORMALIZED_KEY,
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    MERGE_METHOD_PARSED_IDENTITY_APPLICANT,
    MERGE_RUN_STATUS_APPLIED,
    MERGE_RUN_STATUS_PLANNED,
    MERGE_TIER_PARSED_IDENTITY_EXCLUDED,
    MERGE_TIER_PARSED_IDENTITY_SAFE,
    PARSED_IDENTITY_MAX_ROOTS_AUTO_MERGE,
    PARSED_IDENTITY_MIN_PARSE_CONFIDENCE,
)
from db.models import (
    Company,
    CompanyApplicantAlias,
    CompanyCanonicalMergeRollback,
    CompanyCanonicalMergeRun,
)
from pipeline.company_canonical_merge import (
    MergeGroup,
    MergeGroupMember,
    _company_snapshot,
    _member_score,
)
from pipeline.company_fk_remap import remap_company_foreign_keys
from pipeline.company_matching import normalize_vendor_name

MAX_NAME_LEN = 300

_GENERIC_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^demolition\s+ltd\.?$", re.I),
    re.compile(r"^construction\s+inc\.?$", re.I),
    re.compile(r"^contracting\s+ltd\.?$", re.I),
    re.compile(r"^management\s+ltd\.?$", re.I),
    re.compile(r"^excavat", re.I),
    re.compile(r"^building\s+ltd\.?$", re.I),
    re.compile(r"^services\s+ltd\.?$", re.I),
    re.compile(r"^enterprises\s+ltd\.?$", re.I),
)


_GENERIC_BUCKET_COMPANY_NAMES = frozenset(
    {
        "architect",
        "architects",
        "construction",
        "contractor",
        "consultant",
        "consultants",
        "engineer",
        "developer",
        "design",
        "builder",
        "designer",
        "demolition",
        "contracting",
        "management",
        "renovation",
        "excavation",
        "excavating",
    }
)


def is_generic_bucket_company_name(name: str) -> bool:
    """True when companies.name is a single generic profession word (bucket row)."""
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", " ", (name or "").strip()).strip()
    if not cleaned:
        return True
    tokens = cleaned.split()
    if len(tokens) == 1 and tokens[0].casefold() in _GENERIC_BUCKET_COMPANY_NAMES:
        return True
    return cleaned.casefold() in _GENERIC_BUCKET_COMPANY_NAMES


def _company_name_norm_key(name: str) -> str:
    return normalize_vendor_name(name)


def _is_ineligible_company_link(
    company_name: str,
    *,
    norm_key: str,
    total_projects: int,
) -> bool:
    """Drop generic buckets and cross-name permit/company mismatches."""
    if is_generic_bucket_company_name(company_name):
        return True
    if _company_name_norm_key(company_name) != norm_key:
        # Zero-project rows are always junk placeholders; non-zero mismatches
        # indicate permits attached to the wrong company_id.
        return True
    return False


def is_generic_business_name(business_name: str) -> bool:
    name = (business_name or "").strip()
    if len(name) < 12:
        return True
    for pattern in _GENERIC_NAME_PATTERNS:
        if pattern.search(name):
            return True
    tokens = re.sub(r"[^a-zA-Z0-9 ]", " ", name).split()
    if len(tokens) <= 2 and any(
        token.lower()
        in (
            "ltd",
            "inc",
            "corp",
            "limited",
            "construction",
            "contracting",
            "demolition",
            "management",
            "services",
            "enterprises",
        )
        for token in tokens
    ):
        return True
    return False


@dataclass
class ParsedIdentityRootSummary:
    root_id: int
    total_projects: int
    total_value: float
    total_award_value: int
    company_ids: list[int] = field(default_factory=list)
    company_names: list[str] = field(default_factory=list)


@dataclass
class ParsedIdentityMergeGroupReport:
    norm_key: str
    rep_business_name: str
    business_name_variants: list[str]
    distinct_roots: int
    distinct_companies: int
    pi_row_count: int
    tier: str
    exclusion_reason: str
    primary_company_id: int | None
    primary_company_name: str
    roots: list[ParsedIdentityRootSummary]
    members: list[MergeGroupMember] = field(default_factory=list)


@dataclass
class ParsedIdentityMergePlan:
    generated_at: str
    scenario: str
    groups: list[MergeGroup]
    group_reports: list[ParsedIdentityMergeGroupReport]
    excluded_groups: list[ParsedIdentityMergeGroupReport]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_report_dict(self) -> dict[str, Any]:
        safe_reports = [g for g in self.group_reports if g.tier == MERGE_TIER_PARSED_IDENTITY_SAFE]
        return {
            "generated_at": self.generated_at,
            "scenario": self.scenario,
            "summary": self.summary,
            "merge_groups": [self._report_group_dict(group) for group in self.groups],
            "merge_group_details": [self._detail_dict(report) for report in safe_reports],
            "excluded_groups": [self._detail_dict(report) for report in self.excluded_groups],
        }

    @staticmethod
    def _report_group_dict(group: MergeGroup) -> dict[str, Any]:
        return {
            "canonical_key": group.canonical_key,
            "display_name": group.display_name,
            "confidence": group.confidence,
            "method": group.method,
            "primary_company_id": group.primary_company_id,
            "create_canonical_row": group.create_canonical_row,
            "member_count": len(group.members),
            "members": [asdict(member) for member in group.members],
        }

    @staticmethod
    def _detail_dict(report: ParsedIdentityMergeGroupReport) -> dict[str, Any]:
        return {
            "norm_key": report.norm_key,
            "rep_business_name": report.rep_business_name,
            "business_name_variants": report.business_name_variants,
            "distinct_roots": report.distinct_roots,
            "distinct_companies": report.distinct_companies,
            "pi_row_count": report.pi_row_count,
            "tier": report.tier,
            "exclusion_reason": report.exclusion_reason,
            "primary_company_id": report.primary_company_id,
            "primary_company_name": report.primary_company_name,
            "roots": [asdict(root) for root in report.roots],
            "members": [asdict(member) for member in report.members],
        }


def _clamp_name(value: str) -> str:
    return (value or "").strip()[:MAX_NAME_LEN]


def _load_pi_company_links(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
                p.company_id,
                pi.business_name,
                COALESCE(c.canonical_company_id, c.id) AS canonical_root,
                c.name AS company_name,
                c.entity_role,
                COALESCE(c.total_projects, 0) AS total_projects,
                COALESCE(c.total_value, 0) AS total_value,
                COALESCE(c.total_award_value, 0) AS total_award_value,
                COALESCE(c.award_count, 0) AS award_count
            FROM parsed_identities pi
            JOIN permits p
              ON pi.source_type = 'permit'
             AND pi.source_id = p.id
            JOIN companies c ON c.id = p.company_id
            WHERE pi.field_name = 'applicant'
              AND pi.parse_confidence >= :min_conf
              AND pi.business_name IS NOT NULL
              AND btrim(pi.business_name) <> ''
              AND p.company_id IS NOT NULL
            """
        ),
        {"min_conf": PARSED_IDENTITY_MIN_PARSE_CONFIDENCE},
    ).all()
    return [dict(row._mapping) for row in rows]


def _choose_primary_member(members: list[MergeGroupMember]) -> MergeGroupMember:
    eligible = [member for member in members if not is_generic_bucket_company_name(member.name)]
    pool = eligible or members
    return sorted(pool, key=_member_score, reverse=True)[0]


def _choose_primary_root(members: list[MergeGroupMember], company_rows: dict[int, dict[str, Any]]) -> int:
    scores: Counter[int] = Counter()
    for member in members:
        if is_generic_bucket_company_name(member.name):
            continue
        root = int(company_rows[member.company_id]["canonical_root"])
        scores[root] += member.total_projects
    if not scores:
        for member in members:
            root = int(company_rows[member.company_id]["canonical_root"])
            scores[root] += member.total_projects
    return max(scores, key=lambda root: (scores[root], -root))


def build_parsed_identity_merge_plan(session: Session) -> ParsedIdentityMergePlan:
    links = _load_pi_company_links(session)
    key_companies: dict[str, set[int]] = defaultdict(set)
    key_bnames: dict[str, set[str]] = defaultdict(set)
    key_pi_rows: dict[str, int] = defaultdict(int)
    company_rows: dict[int, dict[str, Any]] = {}

    skipped_ineligible_links = 0
    for row in links:
        company_id = int(row["company_id"])
        business_name = str(row["business_name"])
        norm_key = normalize_vendor_name(business_name)
        if not norm_key:
            continue
        company_name = str(row["company_name"])
        if _is_ineligible_company_link(
            company_name,
            norm_key=norm_key,
            total_projects=int(row["total_projects"]),
        ):
            skipped_ineligible_links += 1
            continue
        key_companies[norm_key].add(company_id)
        key_bnames[norm_key].add(business_name)
        key_pi_rows[norm_key] += 1
        company_rows[company_id] = row

    merge_groups: list[MergeGroup] = []
    group_reports: list[ParsedIdentityMergeGroupReport] = []
    excluded_groups: list[ParsedIdentityMergeGroupReport] = []

    for norm_key, company_ids in sorted(key_companies.items()):
        members: list[MergeGroupMember] = []
        for company_id in sorted(company_ids):
            row = company_rows[company_id]
            members.append(
                MergeGroupMember(
                    company_id=company_id,
                    name=str(row["company_name"]),
                    signatory="",
                    has_dba=False,
                    total_projects=int(row["total_projects"]),
                    total_value=float(row["total_value"]),
                    total_award_value=float(row["total_award_value"]),
                    award_count=int(row["award_count"]),
                    confidence=CONFIDENCE_NORMALIZED_KEY,
                    method=MERGE_METHOD_PARSED_IDENTITY_APPLICANT,
                )
            )

        roots_map: dict[int, ParsedIdentityRootSummary] = {}
        for member in members:
            root_id = int(company_rows[member.company_id]["canonical_root"])
            if root_id not in roots_map:
                roots_map[root_id] = ParsedIdentityRootSummary(
                    root_id=root_id,
                    total_projects=0,
                    total_value=0.0,
                    total_award_value=0,
                )
            root = roots_map[root_id]
            root.total_projects += member.total_projects
            root.total_value += member.total_value
            root.total_award_value += int(member.total_award_value)
            root.company_ids.append(member.company_id)
            root.company_names.append(member.name)

        distinct_roots = len(roots_map)
        business_variants = sorted(key_bnames[norm_key], key=len)
        rep_name = max(business_variants, key=len) if business_variants else norm_key
        generic = any(is_generic_business_name(name) for name in key_bnames[norm_key])

        exclusion_reason = ""
        tier = MERGE_TIER_PARSED_IDENTITY_SAFE
        if distinct_roots <= 1:
            tier = MERGE_TIER_PARSED_IDENTITY_EXCLUDED
            exclusion_reason = "single_root"
        elif distinct_roots >= PARSED_IDENTITY_MAX_ROOTS_AUTO_MERGE:
            tier = MERGE_TIER_PARSED_IDENTITY_EXCLUDED
            exclusion_reason = "roots>=100"
        elif generic:
            tier = MERGE_TIER_PARSED_IDENTITY_EXCLUDED
            exclusion_reason = "generic_name"

        primary_member: MergeGroupMember | None = None
        if tier == MERGE_TIER_PARSED_IDENTITY_SAFE:
            primary_root = _choose_primary_root(members, company_rows)
            root_members = [
                member
                for member in members
                if int(company_rows[member.company_id]["canonical_root"]) == primary_root
            ]
            primary_member = _choose_primary_member(root_members)
            if is_generic_bucket_company_name(primary_member.name) and not is_generic_business_name(
                rep_name
            ):
                tier = MERGE_TIER_PARSED_IDENTITY_EXCLUDED
                exclusion_reason = "generic_bucket_winner"
            else:
                merge_groups.append(
                    MergeGroup(
                        canonical_key=norm_key,
                        display_name=_clamp_name(rep_name),
                        confidence=CONFIDENCE_NORMALIZED_KEY,
                        method=MERGE_METHOD_PARSED_IDENTITY_APPLICANT,
                        members=members,
                        primary_company_id=primary_member.company_id,
                        create_canonical_row=False,
                        canonical_name_for_insert="",
                    )
                )

        report = ParsedIdentityMergeGroupReport(
            norm_key=norm_key,
            rep_business_name=rep_name,
            business_name_variants=business_variants,
            distinct_roots=distinct_roots,
            distinct_companies=len(members),
            pi_row_count=key_pi_rows[norm_key],
            tier=tier,
            exclusion_reason=exclusion_reason,
            primary_company_id=primary_member.company_id if primary_member else None,
            primary_company_name=primary_member.name if primary_member else "",
            roots=sorted(roots_map.values(), key=lambda item: (-item.total_projects, item.root_id)),
            members=members,
        )
        if tier == MERGE_TIER_PARSED_IDENTITY_SAFE:
            group_reports.append(report)
        elif distinct_roots > 1:
            excluded_groups.append(report)

    alias_count = sum(len(group.members) - 1 for group in merge_groups)
    in_scope = len(company_rows)
    before_roots = len({int(row["canonical_root"]) for row in company_rows.values()})

    return ParsedIdentityMergePlan(
        generated_at=datetime.now(timezone.utc).isoformat(),
        scenario="parsed_identity_applicant_scenario_b",
        groups=merge_groups,
        group_reports=group_reports,
        excluded_groups=excluded_groups,
        summary={
            "in_scope_companies": in_scope,
            "before_distinct_canonical_roots": before_roots,
            "safe_merge_groups": len(merge_groups),
            "excluded_multi_root_groups": len(excluded_groups),
            "companies_to_repoint_as_aliases": alias_count,
            "creates_new_company_rows": 0,
            "deletes_company_rows": 0,
            "field_name_filter": "applicant",
            "parse_confidence_min": PARSED_IDENTITY_MIN_PARSE_CONFIDENCE,
            "max_roots_auto_merge": PARSED_IDENTITY_MAX_ROOTS_AUTO_MERGE - 1,
            "skipped_ineligible_company_links": skipped_ineligible_links,
        },
    )


def format_parsed_identity_merge_summary(plan: ParsedIdentityMergePlan) -> str:
    summary = plan.summary
    lines = [
        "Parsed identity merge plan (Scenario B — applicant field only)",
        f"  In-scope companies: {summary['in_scope_companies']}",
        f"  Distinct roots before: {summary['before_distinct_canonical_roots']}",
        f"  Safe merge groups: {summary['safe_merge_groups']}",
        f"  Companies to repoint as aliases: {summary['companies_to_repoint_as_aliases']}",
        f"  Excluded multi-root groups: {summary['excluded_multi_root_groups']}",
        f"  New company rows: {summary['creates_new_company_rows']}",
        f"  Deleted company rows: {summary['deletes_company_rows']}",
    ]
    return "\n".join(lines)


def write_parsed_identity_review_markdown(plan: ParsedIdentityMergePlan, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = sorted(plan.group_reports, key=lambda item: (-item.distinct_roots, -item.distinct_companies))
    lines = [
        "# Parsed Identity Merge — Full Review List (Scenario B)",
        "",
        f"**Generated:** {plan.generated_at}  ",
        f"**Safe merge groups:** {len(safe)}  ",
        f"**Excluded multi-root groups:** {len(plan.excluded_groups)}  ",
        "",
        "Review every safe group below before `--apply`.",
        "",
    ]
    for index, group in enumerate(safe, 1):
        lines.append(f"## {index}. {group.rep_business_name}")
        lines.append("")
        lines.append(f"- **Norm key:** `{group.norm_key}`")
        lines.append(f"- **Distinct roots:** {group.distinct_roots}")
        lines.append(f"- **Distinct companies:** {group.distinct_companies}")
        lines.append(f"- **PI applicant rows:** {group.pi_row_count}")
        lines.append(
            f"- **Winning canonical company:** {group.primary_company_id} — {group.primary_company_name}"
        )
        lines.append("")
        lines.append("| Root ID | Total projects | Companies | Names |")
        lines.append("|--------:|---------------:|----------:|-------|")
        for root in group.roots:
            names = "; ".join(root.company_names[:3])
            if len(root.company_names) > 3:
                names += f" (+{len(root.company_names) - 3} more)"
            lines.append(
                f"| {root.root_id} | {root.total_projects} | {len(root.company_ids)} | {names} |"
            )
        lines.append("")

    if plan.excluded_groups:
        lines.append("---")
        lines.append("")
        lines.append("## Excluded groups (manual review only)")
        lines.append("")
        for group in sorted(plan.excluded_groups, key=lambda item: -item.distinct_roots)[:40]:
            lines.append(
                f"- **{group.rep_business_name}** (`{group.norm_key}`): "
                f"{group.distinct_roots} roots — {group.exclusion_reason}"
            )

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def apply_parsed_identity_merge_plan(
    session: Session,
    plan: ParsedIdentityMergePlan,
) -> CompanyCanonicalMergeRun:
    """Apply Scenario B merge plan. Alias reclassification + FK remap only."""
    for group in plan.groups:
        if group.create_canonical_row:
            raise ValueError("Scenario B forbids create_canonical_row")

    run = CompanyCanonicalMergeRun(
        status=MERGE_RUN_STATUS_PLANNED,
        dry_run=False,
        report_json=plan.to_report_dict(),
        summary_json=plan.summary,
    )
    session.add(run)
    session.flush()

    alias_to_canonical: dict[int, int] = {}

    print(f"[ParsedIdentityMergeApply] applying {len(plan.groups)} safe groups...")
    for group in plan.groups:
        primary_id = int(group.primary_company_id or 0)
        if not primary_id:
            continue

        primary = session.get(Company, primary_id)
        if primary is None:
            continue

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
            alias_row.applicant_signatory = ""
            alias_row.canonical_merge_confidence = member.confidence
            alias_row.canonical_merge_method = member.method
            alias_to_canonical[member.company_id] = primary_id

            session.add(
                CompanyApplicantAlias(
                    canonical_company_id=primary_id,
                    alias_company_id=member.company_id,
                    applicant_name_raw=member.name,
                    signatory_name="",
                    merge_run_id=run.id,
                    confidence=member.confidence,
                    merge_method=member.method,
                )
            )

    fk_rollback_rows: list[dict[str, Any]] = []
    print(f"[ParsedIdentityMergeApply] FK remap for {len(alias_to_canonical)} alias ids...")
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

    run.status = MERGE_RUN_STATUS_APPLIED
    run.finished_at = datetime.now(timezone.utc)
    run.summary_json = {
        **plan.summary,
        "run_applied": True,
        "applied_tier": MERGE_TIER_PARSED_IDENTITY_SAFE,
        "applied_merge_groups": len(plan.groups),
        "inserted_canonical_company_ids": [],
        "fk_remap": fk_summary,
    }
    session.commit()
    return run


def build_post_apply_audit(session: Session, *, merge_run_id: int) -> dict[str, Any]:
    """Post-apply conservation checks for Scenario B."""
    counts = {
        str(row[0]): int(row[1])
        for row in session.execute(
            text(
                """
                SELECT entity_role, COUNT(*) AS n
                FROM companies
                GROUP BY entity_role
                ORDER BY entity_role
                """
            )
        ).all()
    }
    new_aliases = int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM company_applicant_aliases
                WHERE merge_run_id = :run_id
                """
            ),
            {"run_id": merge_run_id},
        ).scalar_one()
    )
    totals = session.execute(
        text(
            """
            SELECT
                COALESCE(SUM(total_projects), 0) AS total_projects,
                COALESCE(SUM(total_value), 0) AS total_value,
                COALESCE(SUM(total_award_value), 0) AS total_award_value,
                COALESCE(SUM(award_count), 0) AS award_count
            FROM companies
            """
        )
    ).one()
    permit_links = int(session.execute(text("SELECT COUNT(*) FROM permits WHERE company_id IS NOT NULL")).scalar_one())
    award_links = int(
        session.execute(text("SELECT COUNT(*) FROM contract_awards WHERE company_id IS NOT NULL")).scalar_one()
    )
    orphaned = session.execute(
        text(
            """
            SELECT c.id, c.name, c.entity_role
            FROM companies c
            LEFT JOIN permits p ON p.company_id = c.id
            LEFT JOIN contract_awards ca ON ca.company_id = c.id
            WHERE p.id IS NULL
              AND ca.id IS NULL
              AND c.entity_role = 'applicant_alias'
            ORDER BY c.id
            LIMIT 50
            """
        )
    ).all()
    return {
        "merge_run_id": merge_run_id,
        "entity_role_counts": counts,
        "new_aliases_from_run": new_aliases,
        "registry_totals": dict(totals._mapping),
        "permit_links": permit_links,
        "award_links": award_links,
        "orphaned_alias_samples": [dict(row._mapping) for row in orphaned],
    }
