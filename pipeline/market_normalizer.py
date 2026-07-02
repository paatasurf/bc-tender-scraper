"""Normalize market records into a unified opportunity shape."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from db.models import ArchTender, CommercialTender, ContractAward, Permit, Tender
from pipeline.business_attributes import (
    buyer_type_from_org,
    infer_delivery,
    infer_orientation,
    infer_sector,
    infer_sector_from_permit,
)
from pipeline.capability_profile import CapabilityProfile
from pipeline.company_matching import normalize_vendor_name
from pipeline.scoring.revenue_rank import parse_estimated_value
from pipeline.taxonomy import SOURCE_TO_SEGMENT, tag_opportunity_text

Category = Literal["active", "pipeline", "intelligence", "relationship", "growth"]


@dataclass
class NormalizedOpportunity:
    category: Category
    subtype: str
    source_table: str
    source_id: int
    title: str
    organization: str
    text_blob: str
    trade_tags: list[str]
    project_type_tags: list[str]
    market_segment: str
    estimated_value: float
    geography_text: str
    deadline: str
    is_open: bool
    payload: dict[str, Any]
    context: str = ""
    delivery_type: str = ""
    sector: str = ""
    buyer_type: str = ""
    orientation: str = ""


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    cleaned = value.replace("/", "-").strip()[:10]
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None


def deadline_is_open(deadline: str) -> bool:
    """Legacy string-date open check — distinct from Tender.is_open lifecycle column."""
    parsed = _parse_date(deadline)
    if parsed is None:
        return True
    return parsed >= date.today()


def tender_lifecycle_eligible(row: Any, deadline: str, *, include_closed: bool = False) -> bool:
    """Lifecycle column AND legacy deadline check when include_closed is false."""
    if include_closed:
        return True
    return bool(getattr(row, "is_open", True)) and deadline_is_open(deadline)


def infer_buyer_level(source: str, org: str = "") -> str:
    src = (source or "").lower()
    if src in SOURCE_TO_SEGMENT:
        return SOURCE_TO_SEGMENT[src]
    org_lower = (org or "").lower()
    if "city of" in org_lower or "district of" in org_lower or "municipality" in org_lower:
        return "municipal"
    if "province" in org_lower or "bc " in org_lower:
        return "provincial"
    if "canada" in org_lower or "federal" in org_lower:
        return "federal"
    return "commercial"


def _tender_record(row: Any, source: str) -> NormalizedOpportunity:
    if source == "federal":
        org = row.organization or ""
        deadline = row.closing_date or ""
        value = parse_estimated_value(getattr(row, "estimated_value_numeric", None), parse_estimated_value(row.estimated_value))
        category_label = row.category or ""
        table = "tenders"
        subtype = "federal_tender"
        segment = "federal"
    elif source == "arch":
        org = row.company or ""
        deadline = row.deadline or ""
        value = parse_estimated_value(getattr(row, "estimated_value_numeric", None), parse_estimated_value(row.value))
        category_label = row.category or ""
        table = "arch_tenders"
        subtype = "design_rfp"
        segment = "commercial"
    else:
        org = row.company or ""
        deadline = row.deadline or ""
        value = parse_estimated_value(getattr(row, "estimated_value_numeric", None), parse_estimated_value(row.value))
        category_label = row.category or ""
        table = "commercial_tenders"
        src = (row.source or "").lower()
        subtype = "municipal_tender" if src == "civicinfo" else "commercial_rfp"
        segment = infer_buyer_level(src, org)

    title = row.title or ""
    blob = " ".join(filter(None, [title, category_label, org]))
    tags = tag_opportunity_text(title=title, category=category_label, source=source or segment)
    delivery = infer_delivery(blob)
    sector = infer_sector(blob)
    buyer = infer_buyer_level(source if source != "commercial" else segment, org)
    orientation = "maintenance" if infer_orientation([blob]) == "maintenance" else (
        "design" if delivery == "design" else "construction"
    )
    payload = {
        "id": row.id,
        "title": title,
        "company": org,
        "value": value,
        "deadline": (deadline or "").replace("/", "-")[:10],
        "category": category_label or "Uncategorized",
        "budget_estimate": (getattr(row, "ai_budget_estimate", "") or "").strip() or None,
        "url": getattr(row, "url", "") or "",
        "tender_source": source if source != "commercial" else "commercial",
    }
    return NormalizedOpportunity(
        category="active",
        subtype=subtype,
        source_table=table,
        source_id=row.id,
        title=title,
        organization=org,
        text_blob=" ".join(filter(None, [title, category_label, org])),
        trade_tags=tags.trade_tags,
        project_type_tags=tags.project_type_tags,
        market_segment=segment or tags.market_segment,
        estimated_value=value,
        geography_text=f"{getattr(row, 'location', '')} {org}",
        deadline=payload["deadline"],
        is_open=deadline_is_open(payload["deadline"]),
        payload=payload,
        context="open_tender",
        delivery_type=delivery,
        sector=sector,
        buyer_type=buyer,
        orientation=orientation,
    )


def _permit_record(permit: Permit, *, own: bool) -> NormalizedOpportunity:
    value = parse_estimated_value(permit.project_value)
    blob = " ".join(filter(None, [permit.permit_type, permit.address, permit.description]))
    delivery = infer_delivery(blob, permit.permit_type or "")
    sector = infer_sector_from_permit(
        permit.permit_type or "",
        permit.description or "",
        permit.address or "",
    )
    tags = tag_opportunity_text(
        title=permit.description or "",
        category=permit.permit_type or "",
        permit_type=permit.permit_type or "",
    )
    payload = {
        "id": permit.id,
        "address": permit.address,
        "type": permit.permit_type,
        "value": value,
        "date": (permit.issue_date or "").replace("/", "-")[:10],
        "status": "Issued" if permit.issue_date else "Pending",
        "applicant": permit.applicant,
        "description": permit.description,
        "architect": permit.architect,
    }
    return NormalizedOpportunity(
        category="pipeline",
        subtype="building_permit",
        source_table="permits",
        source_id=permit.id,
        title=permit.address or permit.permit_type or "Building permit",
        organization=permit.applicant or "",
        text_blob=" ".join(filter(None, [permit.permit_type, permit.address, permit.description])),
        trade_tags=tags.trade_tags,
        project_type_tags=tags.project_type_tags,
        market_segment="municipal",
        estimated_value=value,
        geography_text=permit.address or "",
        deadline=payload["date"],
        is_open=True,
        payload=payload,
        context="own_permit" if own else "market_permit",
        delivery_type=delivery,
        sector=sector,
        buyer_type="municipal",
        orientation="construction",
    )


def _award_record(award: ContractAward, *, context: str) -> NormalizedOpportunity:
    value = float(award.award_value or 0)
    blob = " ".join(
        filter(
            None,
            [award.title, award.description, award.procurement_category, award.buyer_organization],
        )
    )
    delivery = infer_delivery(blob)
    sector = infer_sector(blob)
    buyer = award.buyer_level or buyer_type_from_org(award.buyer_organization or "", award.source or "")
    orientation = "maintenance" if infer_orientation([blob]) == "maintenance" else "construction"
    tags = tag_opportunity_text(
        title=award.title or "",
        category=award.procurement_category or "",
        description=award.description or "",
        source=award.source or "",
    )
    payload = {
        "id": award.id,
        "title": award.title,
        "award_date": (award.award_date or "").replace("/", "-")[:10],
        "client": award.buyer_organization,
        "category": award.procurement_category,
        "value": value if value else None,
        "currency": award.currency or "CAD",
        "winner_company": award.winner_company,
        "source": award.source,
        "url": award.url,
        "buyer_level": award.buyer_level,
    }
    return NormalizedOpportunity(
        category="intelligence",
        subtype="contract_award",
        source_table="contract_awards",
        source_id=award.id,
        title=award.title or "",
        organization=award.buyer_organization or "",
        text_blob=" ".join(
            filter(
                None,
                [award.title, award.description, award.procurement_category, award.buyer_organization],
            )
        ),
        trade_tags=tags.trade_tags,
        project_type_tags=tags.project_type_tags,
        market_segment=award.buyer_level or tags.market_segment,
        estimated_value=value,
        geography_text=award.delivery_region or award.buyer_organization or "",
        deadline=payload["award_date"],
        is_open=True,
        payload=payload,
        context=context,
        delivery_type=delivery,
        sector=sector,
        buyer_type=buyer,
        orientation=orientation,
    )


def _tender_deadline_text(row: Any) -> str:
    raw = getattr(row, "closing_date", None) or getattr(row, "deadline", "") or ""
    return str(raw).replace("/", "-")[:10]


def _open_tender_query(model: type[Any], limit: int, *, include_closed: bool):
    stmt = select(model).order_by(model.id.desc()).limit(limit)
    if not include_closed:
        stmt = stmt.where(model.is_open.is_(True))
    return stmt


def load_active_tenders(
    session: Session,
    kind: str,
    limit: int = 400,
    *,
    include_closed: bool = False,
) -> list[NormalizedOpportunity]:
    rows: list[NormalizedOpportunity] = []
    if kind == "architecture":
        for row in session.scalars(_open_tender_query(ArchTender, limit, include_closed=include_closed)).all():
            if tender_lifecycle_eligible(row, _tender_deadline_text(row), include_closed=include_closed):
                rows.append(_tender_record(row, "arch"))
        return rows

    for row in session.scalars(_open_tender_query(Tender, limit, include_closed=include_closed)).all():
        if tender_lifecycle_eligible(row, _tender_deadline_text(row), include_closed=include_closed):
            rows.append(_tender_record(row, "federal"))
    for row in session.scalars(
        _open_tender_query(CommercialTender, limit, include_closed=include_closed)
    ).all():
        if tender_lifecycle_eligible(row, _tender_deadline_text(row), include_closed=include_closed):
            rows.append(_tender_record(row, "commercial"))
    return rows


def load_pipeline_permits(
    session: Session,
    profile: CapabilityProfile,
    limit: int = 200,
) -> list[NormalizedOpportunity]:
    results: list[NormalizedOpportunity] = []
    seen: set[int] = set()

    if profile.normalized_name:
        for permit in session.scalars(
            select(Permit).where(Permit.applicant != "").order_by(Permit.id.desc()).limit(limit * 3)
        ).all():
            if normalize_vendor_name(permit.applicant) == profile.normalized_name:
                results.append(_permit_record(permit, own=True))
                seen.add(permit.id)

    type_terms = [t.lower() for t in profile.project_types if t]
    query = select(Permit).order_by(Permit.id.desc()).limit(limit * 3)
    if type_terms:
        clauses = [func.lower(Permit.permit_type).contains(term) for term in type_terms[:6]]
        query = query.where(or_(*clauses))
    for permit in session.scalars(query).all():
        if permit.id in seen:
            continue
        own = (
            profile.normalized_name != ""
            and normalize_vendor_name(permit.applicant) == profile.normalized_name
        )
        results.append(_permit_record(permit, own=own))
        seen.add(permit.id)
        if len(results) >= limit:
            break
    return results[:limit]


def load_intelligence_awards(
    session: Session,
    company_id: int,
    profile: CapabilityProfile,
    limit: int = 200,
) -> list[NormalizedOpportunity]:
    results: list[NormalizedOpportunity] = []
    seen: set[int] = set()

    def add(award: ContractAward, context: str) -> None:
        if award.id in seen:
            return
        results.append(_award_record(award, context=context))
        seen.add(award.id)

    for award in session.scalars(
        select(ContractAward)
        .where(ContractAward.company_id == company_id)
        .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
        .limit(min(limit, 100))
    ).all():
        add(award, "own_history")

    categories = profile.award_categories
    if categories:
        for award in session.scalars(
            select(ContractAward)
            .where(ContractAward.procurement_category.in_(categories[:10]))
            .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .limit(limit)
        ).all():
            add(award, "peer_award")

    clients = [c.strip().lower() for c in profile.award_clients if c.strip()]
    if clients:
        for award in session.scalars(
            select(ContractAward)
            .where(ContractAward.buyer_organization != "")
            .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .limit(limit * 3)
        ).all():
            buyer = (award.buyer_organization or "").lower()
            if any(client in buyer or buyer in client for client in clients):
                add(award, "client_history")
            if len(results) >= limit:
                break

    return results[:limit]
