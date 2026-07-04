"""Manual company bridging for confirmed operator plans (Class C)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    CONFIDENCE_DBA_EXPLICIT,
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    MERGE_METHOD_MANUAL_BRIDGE_LEDCOR,
    MERGE_RUN_STATUS_APPLIED,
    MERGE_RUN_STATUS_PLANNED,
)
from db.models import Company, CompanyApplicantAlias, CompanyCanonicalMergeRollback, CompanyCanonicalMergeRun, Permit
from pipeline.company_canonical_merge import _company_snapshot
from pipeline.company_fk_remap import FK_REMAP_SPECS, remap_company_foreign_keys
from pipeline.company_intelligence import MAX_LIST_ITEMS, _neighborhood_from_address, _parse_value

LEDCOR_CANONICAL_ID = 8756
LEDCOR_ALIAS_IDS = (3046, 302683)
LEDCOR_EXCLUDED_IDS = (134005,)


def _empty_permit_aggregate_entry() -> dict[str, Any]:
    return {
        "total_projects": 0,
        "total_value": 0.0,
        "project_types": Counter(),
        "neighborhoods": Counter(),
        "first_project_date": "",
        "last_project_date": "",
    }


def _accumulate_permit_aggregate_entry(
    entry: dict[str, Any],
    *,
    permit_type: str | None,
    project_value: str | None,
    issue_date: str | None,
    address: str | None,
) -> None:
    entry["total_projects"] += 1
    entry["total_value"] += _parse_value(project_value)
    ptype = (permit_type or "").strip()
    if ptype:
        entry["project_types"][ptype] += 1
    neighborhood = _neighborhood_from_address(address)
    if neighborhood:
        entry["neighborhoods"][neighborhood] += 1
    issue_date = (issue_date or "").strip()
    if issue_date:
        if not entry["first_project_date"] or issue_date < entry["first_project_date"]:
            entry["first_project_date"] = issue_date
        if not entry["last_project_date"] or issue_date > entry["last_project_date"]:
            entry["last_project_date"] = issue_date


def _permit_aggregate_entry_to_public(entry: dict[str, Any]) -> dict[str, Any]:
    total = int(entry["total_projects"])
    total_value = round(float(entry["total_value"]), 2)
    return {
        "total_projects": total,
        "total_value": total_value,
        "avg_project_value": round(total_value / total, 2) if total else 0.0,
        "project_types": [item for item, _ in entry["project_types"].most_common(MAX_LIST_ITEMS)],
        "neighborhoods": [item for item, _ in entry["neighborhoods"].most_common(MAX_LIST_ITEMS)],
        "first_project_date": entry["first_project_date"],
        "last_project_date": entry["last_project_date"],
    }


def compute_company_permit_aggregate_stats(session: Session, company_id: int) -> dict[str, Any]:
    """Read-only permit-derived stats for one company via permits.company_id."""
    entry = _empty_permit_aggregate_entry()
    rows = session.execute(
        select(
            Permit.permit_type,
            Permit.project_value,
            Permit.issue_date,
            Permit.address,
        ).where(Permit.company_id == company_id)
    ).all()
    for permit_type, project_value, issue_date, address in rows:
        _accumulate_permit_aggregate_entry(
            entry,
            permit_type=permit_type,
            project_value=project_value,
            issue_date=issue_date,
            address=address,
        )
    return _permit_aggregate_entry_to_public(entry)


def preview_company_permit_aggregates_after_alias_remap(
    session: Session,
    *,
    canonical_company_id: int,
    alias_company_ids: list[int],
) -> dict[str, Any]:
    """Simulate canonical stats after alias FK rows are remapped onto canonical."""
    entry = _empty_permit_aggregate_entry()
    company_ids = [canonical_company_id, *[cid for cid in alias_company_ids if cid != canonical_company_id]]
    rows = session.execute(
        select(
            Permit.permit_type,
            Permit.project_value,
            Permit.issue_date,
            Permit.address,
        ).where(Permit.company_id.in_(company_ids))
    ).all()
    for permit_type, project_value, issue_date, address in rows:
        _accumulate_permit_aggregate_entry(
            entry,
            permit_type=permit_type,
            project_value=project_value,
            issue_date=issue_date,
            address=address,
        )
    public = _permit_aggregate_entry_to_public(entry)
    public["source_company_ids"] = company_ids
    public["permit_fk_rows_included"] = len(rows)
    return public


def recompute_company_permit_aggregates(session: Session, company_id: int) -> dict[str, Any]:
    """Recompute permit-derived stats on one company from permits.company_id."""
    stats = compute_company_permit_aggregate_stats(session, company_id)
    session.execute(
        update(Company)
        .where(Company.id == company_id)
        .values(
            total_projects=stats["total_projects"],
            total_value=stats["total_value"],
            avg_project_value=stats["avg_project_value"],
            project_types=stats["project_types"],
            neighborhoods=stats["neighborhoods"],
            first_project_date=stats["first_project_date"],
            last_project_date=stats["last_project_date"],
            updated_at=func.now(),
        )
    )
    return stats


@dataclass
class ManualBridgeAliasSpec:
    alias_company_id: int
    applicant_name_raw: str
    signatory_name: str


@dataclass
class LedcorManualBridgePlan:
    operation: str = "ledcor_manual_bridge"
    class_label: str = "C"
    generated_at: str = ""
    canonical_company_id: int = LEDCOR_CANONICAL_ID
    alias_specs: list[ManualBridgeAliasSpec] = field(default_factory=list)
    excluded_company_ids: list[int] = field(default_factory=lambda: list(LEDCOR_EXCLUDED_IDS))
    destructive_delete: bool = False
    canonical_before: dict[str, Any] = field(default_factory=dict)
    canonical_after: dict[str, Any] = field(default_factory=dict)
    alias_before: list[dict[str, Any]] = field(default_factory=list)
    alias_after: list[dict[str, Any]] = field(default_factory=list)
    excluded_unchanged: list[dict[str, Any]] = field(default_factory=list)
    alias_count_before: int = 0
    alias_count_after: int = 0
    alias_evidence_audit: list[dict[str, Any]] = field(default_factory=list)
    aggregate_recompute: dict[str, Any] = field(default_factory=dict)
    fk_remap_preview: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "class": self.class_label,
            "generated_at": self.generated_at,
            "destructive_delete": self.destructive_delete,
            "canonical_company_id": self.canonical_company_id,
            "alias_company_ids": [spec.alias_company_id for spec in self.alias_specs],
            "excluded_company_ids": self.excluded_company_ids,
            "validation_errors": self.validation_errors,
            "notes": self.notes,
            "canonical_before": self.canonical_before,
            "canonical_after": self.canonical_after,
            "alias_count_before": self.alias_count_before,
            "alias_count_after": self.alias_count_after,
            "alias_before": self.alias_before,
            "alias_after": self.alias_after,
            "excluded_unchanged": self.excluded_unchanged,
            "alias_evidence_audit": self.alias_evidence_audit,
            "aggregate_recompute": self.aggregate_recompute,
            "fk_remap_preview": self.fk_remap_preview,
            "alias_specs": [asdict(spec) for spec in self.alias_specs],
        }


def _company_public(company: Company) -> dict[str, Any]:
    return {
        "id": company.id,
        "name": company.name,
        "display_name": company.display_name,
        "entity_role": company.entity_role,
        "canonical_company_id": company.canonical_company_id,
        "applicant_signatory": company.applicant_signatory,
        "canonical_vendor_name": company.canonical_vendor_name,
        "total_projects": int(company.total_projects or 0),
        "total_value": float(company.total_value or 0.0),
        "total_award_value": float(company.total_award_value or 0.0),
        "award_count": int(company.award_count or 0),
        "canonical_merge_method": company.canonical_merge_method,
    }


def _alias_count(session: Session, canonical_id: int) -> int:
    return int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM company_applicant_aliases
                WHERE canonical_company_id = :canonical_id
                """
            ),
            {"canonical_id": canonical_id},
        ).scalar_one()
    )


def _fk_remap_preview(session: Session, alias_ids: list[int]) -> dict[str, Any]:
    preview: dict[str, Any] = {"tables": {}, "total_rows": 0}
    for alias_id in alias_ids:
        for spec in FK_REMAP_SPECS:
            count = int(
                session.execute(
                    text(f"SELECT COUNT(*) FROM {spec.table} WHERE {spec.column} = :alias_id"),
                    {"alias_id": alias_id},
                ).scalar_one()
            )
            if count:
                table = preview["tables"].setdefault(spec.table, {"by_alias_id": {}, "total": 0})
                table["by_alias_id"][str(alias_id)] = count
                table["total"] += count
                preview["total_rows"] += count
    return preview


def _evidence_counts(session: Session, company_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, col in (
        ("permits", "company_id"),
        ("contract_awards", "company_id"),
        ("tender_outcomes", "company_id"),
    ):
        counts[table] = int(
            session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :cid"),
                {"cid": company_id},
            ).scalar_one()
        )
    return counts


def _alias_evidence_audit(
    session: Session,
    *,
    alias_id: int,
    alias_name: str,
    canonical_id: int,
) -> dict[str, Any]:
    stored = session.get(Company, alias_id)
    evidence = _evidence_counts(session, alias_id)
    permit_stats = compute_company_permit_aggregate_stats(session, alias_id)
    related_permits_on_canonical = int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM permits
                WHERE company_id = :canonical_id
                  AND applicant = :applicant_name
                """
            ),
            {"canonical_id": canonical_id, "applicant_name": alias_name},
        ).scalar_one()
    )
    interpretation = "stored stats match linked permit FK evidence"
    if evidence["permits"] == 0 and int(stored.total_projects or 0) > 0:
        if related_permits_on_canonical > 0:
            interpretation = (
                "orphaned company-row stats — permits with matching applicant string are "
                f"already linked to canonical {canonical_id}, not this alias id"
            )
        else:
            interpretation = "stored stats have no permits.company_id backing on alias or canonical"

    return {
        "alias_company_id": alias_id,
        "alias_name": alias_name,
        "stored_total_projects": int(stored.total_projects or 0) if stored else 0,
        "stored_total_value": float(stored.total_value or 0.0) if stored else 0.0,
        "linked_evidence_counts": evidence,
        "permit_fk_derived_stats": permit_stats,
        "matching_permits_on_canonical": related_permits_on_canonical,
        "interpretation": interpretation,
    }


def build_ledcor_manual_bridge_plan(session: Session) -> LedcorManualBridgePlan:
    """Plan Ledcor manual bridge: 3046 + 302683 → alias of 8756; 134005 untouched."""
    plan = LedcorManualBridgePlan(
        generated_at=datetime.now(timezone.utc).isoformat(),
        alias_specs=[
            ManualBridgeAliasSpec(
                alias_company_id=3046,
                applicant_name_raw="Chris Burrows DBA: Ledcor",
                signatory_name="Chris Burrows",
            ),
            ManualBridgeAliasSpec(
                alias_company_id=302683,
                applicant_name_raw=(
                    "500 - 1055 HASTINGS ST W VANCOUVER, BC V6E 2E9LEDCOR CONSTRUCTION LIMITED"
                ),
                signatory_name="",
            ),
        ],
        notes=[
            "134005 Ledcor Highways Ltd. remains standalone — distinct legal entity; "
            "no parent_company_id column yet (future schema task).",
            "Alias rows are reclassified, not deleted.",
            "Canonical permit stats are recomputed via recompute_company_permit_aggregates — "
            "not arithmetically incremented from alias row fields.",
        ],
    )

    canonical = session.get(Company, LEDCOR_CANONICAL_ID)
    if canonical is None:
        plan.validation_errors.append(f"Canonical company {LEDCOR_CANONICAL_ID} not found")
        return plan
    if canonical.entity_role != ENTITY_ROLE_CANONICAL:
        plan.validation_errors.append(
            f"Company {LEDCOR_CANONICAL_ID} entity_role={canonical.entity_role!r}, expected canonical"
        )

    plan.canonical_before = _company_public(canonical)
    plan.alias_count_before = _alias_count(session, LEDCOR_CANONICAL_ID)
    alias_ids = [spec.alias_company_id for spec in plan.alias_specs]

    for spec in plan.alias_specs:
        alias = session.get(Company, spec.alias_company_id)
        if alias is None:
            plan.validation_errors.append(f"Alias company {spec.alias_company_id} not found")
            continue
        before = _company_public(alias)
        plan.alias_before.append(before)
        plan.alias_evidence_audit.append(
            _alias_evidence_audit(
                session,
                alias_id=spec.alias_company_id,
                alias_name=spec.applicant_name_raw,
                canonical_id=LEDCOR_CANONICAL_ID,
            )
        )

        if alias.entity_role == ENTITY_ROLE_APPLICANT_ALIAS and alias.canonical_company_id != LEDCOR_CANONICAL_ID:
            plan.validation_errors.append(
                f"Company {spec.alias_company_id} already aliased to {alias.canonical_company_id}"
            )
        elif alias.entity_role == ENTITY_ROLE_CANONICAL:
            plan.validation_errors.append(f"Company {spec.alias_company_id} is canonical — cannot alias")

        after = {
            **before,
            "entity_role": ENTITY_ROLE_APPLICANT_ALIAS,
            "canonical_company_id": LEDCOR_CANONICAL_ID,
            "display_name": canonical.display_name or canonical.name,
            "applicant_signatory": spec.signatory_name,
            "canonical_merge_method": MERGE_METHOD_MANUAL_BRIDGE_LEDCOR,
            "deleted": False,
        }
        plan.alias_after.append(after)

    for excluded_id in plan.excluded_company_ids:
        excluded = session.get(Company, excluded_id)
        if excluded is None:
            plan.validation_errors.append(f"Excluded company {excluded_id} not found")
            continue
        plan.excluded_unchanged.append(
            {
                **_company_public(excluded),
                "mutation": "none",
                "reason": "distinct legal entity — explicitly excluded from bridge",
            }
        )

    before_stats = compute_company_permit_aggregate_stats(session, LEDCOR_CANONICAL_ID)
    after_stats = preview_company_permit_aggregates_after_alias_remap(
        session,
        canonical_company_id=LEDCOR_CANONICAL_ID,
        alias_company_ids=alias_ids,
    )
    plan.aggregate_recompute = {
        "method": "recompute_company_permit_aggregates",
        "scope": "canonical_only",
        "before_from_permit_fk": before_stats,
        "after_from_permit_fk_post_remap": after_stats,
        "delta_total_projects": after_stats["total_projects"] - before_stats["total_projects"],
        "delta_total_value": round(after_stats["total_value"] - before_stats["total_value"], 2),
    }
    plan.canonical_after = {
        **plan.canonical_before,
        **{
            key: after_stats[key]
            for key in (
                "total_projects",
                "total_value",
                "avg_project_value",
                "project_types",
                "neighborhoods",
                "first_project_date",
                "last_project_date",
            )
        },
        "total_award_value": plan.canonical_before.get("total_award_value", 0.0),
        "award_count": plan.canonical_before.get("award_count", 0),
        "entity_role": ENTITY_ROLE_CANONICAL,
        "canonical_company_id": None,
    }
    plan.alias_count_after = plan.alias_count_before + len(plan.alias_specs)
    plan.fk_remap_preview = _fk_remap_preview(session, alias_ids)
    return plan


def apply_ledcor_manual_bridge_plan(
    session: Session,
    plan: LedcorManualBridgePlan,
) -> dict[str, Any]:
    """Apply Ledcor manual bridge with rollback snapshots."""
    if plan.validation_errors:
        raise ValueError(f"Plan has validation errors: {plan.validation_errors}")

    run = CompanyCanonicalMergeRun(
        status=MERGE_RUN_STATUS_PLANNED,
        dry_run=False,
        report_json=plan.to_report_dict(),
        summary_json={"operation": plan.operation, "class": plan.class_label},
    )
    session.add(run)
    session.flush()

    canonical = session.get(Company, LEDCOR_CANONICAL_ID)
    if canonical is None:
        raise ValueError(f"Canonical company {LEDCOR_CANONICAL_ID} not found")

    session.add(
        CompanyCanonicalMergeRollback(
            run_id=run.id,
            entity_type="company",
            entity_id=LEDCOR_CANONICAL_ID,
            before_json={
                **_company_snapshot(canonical),
                "total_projects": canonical.total_projects,
                "total_value": canonical.total_value,
                "total_award_value": canonical.total_award_value,
                "award_count": canonical.award_count,
            },
        )
    )

    alias_to_canonical: dict[int, int] = {}
    for spec in plan.alias_specs:
        alias = session.get(Company, spec.alias_company_id)
        if alias is None:
            continue

        session.add(
            CompanyCanonicalMergeRollback(
                run_id=run.id,
                entity_type="company",
                entity_id=spec.alias_company_id,
                before_json=_company_snapshot(alias),
            )
        )
        alias.entity_role = ENTITY_ROLE_APPLICANT_ALIAS
        alias.canonical_company_id = LEDCOR_CANONICAL_ID
        alias.display_name = canonical.display_name or canonical.name
        alias.applicant_signatory = spec.signatory_name
        alias.canonical_merge_confidence = CONFIDENCE_DBA_EXPLICIT
        alias.canonical_merge_method = MERGE_METHOD_MANUAL_BRIDGE_LEDCOR
        alias_to_canonical[spec.alias_company_id] = LEDCOR_CANONICAL_ID

        session.add(
            CompanyApplicantAlias(
                canonical_company_id=LEDCOR_CANONICAL_ID,
                alias_company_id=spec.alias_company_id,
                applicant_name_raw=spec.applicant_name_raw,
                signatory_name=spec.signatory_name,
                merge_run_id=run.id,
                confidence=CONFIDENCE_DBA_EXPLICIT,
                merge_method=MERGE_METHOD_MANUAL_BRIDGE_LEDCOR,
            )
        )

    fk_rollback_rows: list[dict[str, Any]] = []
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

    recomputed = recompute_company_permit_aggregates(session, LEDCOR_CANONICAL_ID)

    run.status = MERGE_RUN_STATUS_APPLIED
    run.finished_at = datetime.now(timezone.utc)
    run.summary_json = {
        "operation": plan.operation,
        "class": plan.class_label,
        "applied": True,
        "canonical_company_id": LEDCOR_CANONICAL_ID,
        "alias_company_ids": list(alias_to_canonical.keys()),
        "excluded_company_ids": plan.excluded_company_ids,
        "fk_remap": fk_summary,
        "aggregate_recompute": recomputed,
    }
    merge_run_id = int(run.id)
    merge_status = run.status
    session.commit()
    return {
        "merge_run_id": merge_run_id,
        "status": merge_status,
        "fk_remap": fk_summary,
        "aggregate_recompute": recomputed,
    }
