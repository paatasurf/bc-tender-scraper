"""Registry Engine Stage 2A — Evidence Link readiness audit.

Read-only. Every query in this module is a SELECT (via SQLAlchemy Core
``select()``), executed through ``session.execute()``/``session.scalar()``/
``session.scalars()``. Nothing here calls ``session.add``, ``session.merge``,
``session.delete``, ``session.flush``, or ``session.commit`` — there is
nothing to write.

Per Registry spec Section 10.2: company_registry_links is OrgBook/ODBUS
Registry Evidence only. Activity Evidence Links are permits.company_id and
contract_awards.company_id, which already exist — this module validates
them, it does not create or modify anything.

All broken/cycle/depth_exhausted/excluded totals are computed from
full-dataset aggregate queries, never from the bounded illustrative samples.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from db.company_canonical_constants import ENTITY_ROLE_CANONICAL
from db.models import Company, ContractAward, Permit, Tender
from pipeline.registry_engine.evidence.domain import (
    EVIDENCE_TYPE_CONTRACT_AWARD,
    EVIDENCE_TYPE_PERMIT,
    SOURCE_TABLE_CONTRACT_AWARDS,
    SOURCE_TABLE_PERMITS,
    CanonicalTargetResult,
    EvidenceLinkAuditReport,
    EvidenceReference,
    TenderEvidenceLinkageReport,
)

SAMPLE_LIMIT = 50
REFERENCE_SAMPLE_LIMIT = 200
MAX_REDIRECT_DEPTH = 10
DATASET_HASH_BATCH_SIZE = 1000
REGISTRY_STATUS_EXCLUDED = "excluded"
MISSING_COMPANY_ROLE = "missing_company"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_canonical_targets(
    session: Session,
    distinct_company_ids: list[int],
) -> dict[int, CanonicalTargetResult]:
    """Resolve each distinct non-canonical company_id through its redirect
    chain, with cycle, broken-redirect, and depth-exhaustion detection.
    Read-only. Operates on the full distinct set passed in — callers must
    not pre-truncate this list to a sample.
    """
    if not distinct_company_ids:
        return {}

    company_index: dict[int, Company] = {}

    def _load(ids: set[int]) -> None:
        missing = ids - set(company_index)
        if not missing:
            return
        rows = session.scalars(select(Company).where(Company.id.in_(missing))).all()
        for row in rows:
            company_index[int(row.id)] = row

    _load(set(distinct_company_ids))

    results: dict[int, CanonicalTargetResult] = {}
    for company_id in distinct_company_ids:
        direct = company_index.get(company_id)
        if direct is None:
            results[company_id] = CanonicalTargetResult(
                direct_company_id=company_id,
                direct_entity_role="",
                is_canonical=False,
                resolved_canonical_id=None,
                redirect_broken=True,
                redirect_cycle=False,
                redirect_depth_exhausted=False,
                excluded=False,
            )
            continue

        if direct.entity_role == ENTITY_ROLE_CANONICAL:
            results[company_id] = CanonicalTargetResult(
                direct_company_id=company_id,
                direct_entity_role=direct.entity_role,
                is_canonical=True,
                resolved_canonical_id=company_id,
                redirect_broken=False,
                redirect_cycle=False,
                redirect_depth_exhausted=False,
                excluded=(direct.registry_status == REGISTRY_STATUS_EXCLUDED),
            )
            continue

        visited: set[int] = {company_id}
        current_id = direct.canonical_company_id
        broken = False
        cycle = False
        depth_exhausted = False
        resolved: Company | None = None
        depth = 0
        while current_id is not None:
            if depth >= MAX_REDIRECT_DEPTH:
                depth_exhausted = True
                break
            if current_id in visited:
                cycle = True
                break
            visited.add(current_id)
            _load({current_id})
            current = company_index.get(current_id)
            if current is None:
                broken = True
                break
            if current.entity_role == ENTITY_ROLE_CANONICAL:
                resolved = current
                break
            current_id = current.canonical_company_id
            depth += 1

        results[company_id] = CanonicalTargetResult(
            direct_company_id=company_id,
            direct_entity_role=direct.entity_role,
            is_canonical=False,
            resolved_canonical_id=int(resolved.id) if resolved is not None else None,
            redirect_broken=broken,
            redirect_cycle=cycle,
            redirect_depth_exhausted=depth_exhausted,
            excluded=(
                (resolved.registry_status == REGISTRY_STATUS_EXCLUDED)
                if resolved is not None
                else False
            ),
        )

    return results


def _compute_linked_entity_role_counts(
    session: Session, *, model: type[Any]
) -> dict[str, int]:
    """Full-dataset breakdown of every linked row by the target company's
    entity_role. Read-only, single aggregate query, never derived from or
    bounded by a sample.

    A row whose company_id has no matching Company row (a structural
    orphan) surfaces as a NULL entity_role from the outer join and is
    bucketed under MISSING_COMPANY_ROLE. An entity_role value outside the
    known vocabulary — currently blocked by companies.ck_companies_entity_role,
    but not a guarantee this function should assume — gets its own literal
    key rather than being dropped or merged into another bucket.
    """
    role_rows = session.execute(
        select(Company.entity_role, func.count())
        .select_from(model)
        .outerjoin(Company, Company.id == model.company_id)
        .where(model.company_id.isnot(None))
        .group_by(Company.entity_role)
    ).all()
    return dict(
        sorted(
            (
                str(role) if role is not None else MISSING_COMPANY_ROLE,
                int(count),
            )
            for role, count in role_rows
        )
    )


def _compute_dataset_hash(
    session: Session,
    *,
    model: type[Any],
    source_table: str,
    source_system_col: Any,
    external_id_col: Any,
    scraped_at_col: Any,
) -> str:
    """Streamed, batched, full-dataset hash. Never truncated to a sample —
    this is the field that determines report integrity, not report_hash.
    """
    hasher = hashlib.sha256()
    stmt = (
        select(
            model.id,
            source_system_col,
            external_id_col,
            model.company_id,
            scraped_at_col,
        )
        .where(model.company_id.isnot(None))
        .order_by(model.id)
    )
    result = session.execute(stmt)
    for row in result.yield_per(DATASET_HASH_BATCH_SIZE):
        internal_id, source_system, external_id, company_id, ts = row
        ts_str = ts.isoformat() if ts is not None else ""
        part = "|".join(
            [
                source_table,
                str(source_system or ""),
                str(external_id or ""),
                str(internal_id),
                str(company_id),
                ts_str,
            ]
        )
        hasher.update(part.encode("utf-8"))
    return hasher.hexdigest()


def _audit_evidence_source(
    session: Session,
    *,
    source: str,
    evidence_type: str,
    source_table: str,
    model: type[Any],
    source_system_col: Any,
    external_id_col: Any,
    scraped_at_col: Any,
) -> EvidenceLinkAuditReport:
    total_rows = session.scalar(select(func.count()).select_from(model)) or 0
    rows_with_company_id = (
        session.scalar(
            select(func.count()).select_from(model).where(model.company_id.isnot(None))
        )
        or 0
    )
    rows_without_company_id = total_rows - rows_with_company_id

    linked_entity_role_counts = _compute_linked_entity_role_counts(session, model=model)

    orphan_count = (
        session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.company_id.isnot(None))
            .where(~exists().where(Company.id == model.company_id))
        )
        or 0
    )
    orphan_rows = session.execute(
        select(model.id, model.company_id)
        .where(model.company_id.isnot(None))
        .where(~exists().where(Company.id == model.company_id))
        .limit(SAMPLE_LIMIT)
    ).all()
    orphan_samples = [
        {"internal_id": int(r.id), "company_id": int(r.company_id)} for r in orphan_rows
    ]

    # Direct canonical links whose target is itself excluded — a separate
    # population from the non-canonical/redirect population below.
    direct_canonical_excluded_count = (
        session.scalar(
            select(func.count())
            .select_from(model)
            .join(Company, Company.id == model.company_id)
            .where(Company.entity_role == ENTITY_ROLE_CANONICAL)
            .where(Company.registry_status == REGISTRY_STATUS_EXCLUDED)
        )
        or 0
    )

    # Full-dataset (unlimited) row-count per non-canonical company_id.
    # Bounded by the number of distinct companies, not by row count.
    noncanonical_row_counts = session.execute(
        select(Company.id, func.count())
        .select_from(model)
        .join(Company, Company.id == model.company_id)
        .where(Company.entity_role != ENTITY_ROLE_CANONICAL)
        .group_by(Company.id)
    ).all()
    row_count_by_company_id = {
        int(cid): int(cnt) for cid, cnt in noncanonical_row_counts
    }
    non_canonical_count = sum(row_count_by_company_id.values())

    distinct_noncanonical_ids = sorted(row_count_by_company_id.keys())
    resolved_targets = _resolve_canonical_targets(session, distinct_noncanonical_ids)

    broken_redirect_count = 0
    cycle_count = 0
    depth_exhausted_count = 0
    excluded_target_count = direct_canonical_excluded_count
    for company_id, row_count in row_count_by_company_id.items():
        target = resolved_targets.get(company_id)
        if target is None:
            continue
        if target.redirect_broken:
            broken_redirect_count += row_count
        if target.redirect_cycle:
            cycle_count += row_count
        if target.redirect_depth_exhausted:
            depth_exhausted_count += row_count
        if target.excluded:
            excluded_target_count += row_count

    # Bounded illustrative sample only — never used for the totals above.
    non_canonical_rows = session.execute(
        select(model.id, model.company_id, Company.entity_role)
        .select_from(model)
        .join(Company, Company.id == model.company_id)
        .where(Company.entity_role != ENTITY_ROLE_CANONICAL)
        .limit(SAMPLE_LIMIT)
    ).all()
    non_canonical_samples: list[dict[str, Any]] = []
    for row in non_canonical_rows:
        target = resolved_targets.get(int(row.company_id))
        non_canonical_samples.append(
            {
                "internal_id": int(row.id),
                "company_id": int(row.company_id),
                "direct_entity_role": row.entity_role,
                "resolved_canonical_id": (
                    target.resolved_canonical_id if target else None
                ),
                "redirect_broken": target.redirect_broken if target else False,
                "redirect_cycle": target.redirect_cycle if target else False,
                "redirect_depth_exhausted": (
                    target.redirect_depth_exhausted if target else False
                ),
                "excluded": target.excluded if target else False,
            }
        )

    reference_rows = session.execute(
        select(
            model.id,
            source_system_col,
            external_id_col,
            model.company_id,
            scraped_at_col,
        )
        .where(model.company_id.isnot(None))
        .order_by(model.id)
        .limit(REFERENCE_SAMPLE_LIMIT)
    ).all()
    reference_sample = [
        EvidenceReference(
            evidence_type=evidence_type,
            source_table=source_table,
            source_system=str(r[1] or ""),
            external_id=str(r[2] or ""),
            internal_id=int(r[0]),
            company_id=int(r[3]),
            timestamp=r[4].isoformat() if r[4] is not None else "",
        )
        for r in reference_rows
    ]

    dataset_hash = _compute_dataset_hash(
        session,
        model=model,
        source_table=source_table,
        source_system_col=source_system_col,
        external_id_col=external_id_col,
        scraped_at_col=scraped_at_col,
    )

    return EvidenceLinkAuditReport(
        source=source,
        generated_at=_now_iso(),
        total_rows=total_rows,
        rows_with_company_id=rows_with_company_id,
        rows_without_company_id=rows_without_company_id,
        orphan_count=orphan_count,
        orphan_samples=orphan_samples,
        non_canonical_count=non_canonical_count,
        non_canonical_samples=non_canonical_samples,
        broken_redirect_count=broken_redirect_count,
        cycle_count=cycle_count,
        depth_exhausted_count=depth_exhausted_count,
        excluded_target_count=excluded_target_count,
        reference_sample=reference_sample,
        dataset_hash=dataset_hash,
        linked_entity_role_counts=linked_entity_role_counts,
    )


def audit_permit_evidence_links(session: Session) -> EvidenceLinkAuditReport:
    return _audit_evidence_source(
        session,
        source=EVIDENCE_TYPE_PERMIT,
        evidence_type=EVIDENCE_TYPE_PERMIT,
        source_table=SOURCE_TABLE_PERMITS,
        model=Permit,
        source_system_col=Permit.source,
        external_id_col=Permit.external_id,
        scraped_at_col=Permit.scraped_at,
    )


def audit_contract_award_evidence_links(session: Session) -> EvidenceLinkAuditReport:
    return _audit_evidence_source(
        session,
        source=EVIDENCE_TYPE_CONTRACT_AWARD,
        evidence_type=EVIDENCE_TYPE_CONTRACT_AWARD,
        source_table=SOURCE_TABLE_CONTRACT_AWARDS,
        model=ContractAward,
        source_system_col=ContractAward.source,
        external_id_col=ContractAward.external_id,
        scraped_at_col=ContractAward.scraped_at,
    )


def audit_tender_evidence_linkage(session: Session) -> TenderEvidenceLinkageReport:
    """Reports the tenders.company_id schema gap. Does not create the column."""
    has_company_id_column = "company_id" in Tender.__table__.columns
    total_tenders = session.scalar(select(func.count()).select_from(Tender)) or 0
    return TenderEvidenceLinkageReport(
        total_tenders=total_tenders,
        has_company_id_column=has_company_id_column,
        schema_gap=not has_company_id_column,
        note=(
            "tenders has no company_id column — Evidence Link (activity) for "
            "tenders does not exist yet. Per spec.md Section 10.2 this is a "
            "known future migration, out of scope for Stage 2A."
        ),
    )
