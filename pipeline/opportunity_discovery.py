"""Internal (non-AI) opportunity discovery for company profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from db.models import (
    ArchCompany,
    ArchTender,
    CommercialTender,
    Company,
    ContractAward,
    Permit,
    Tender,
)
from pipeline.company_matching import normalize_vendor_name

Kind = Literal["construction", "architecture"]
OpportunityType = Literal["tender", "permit", "contract_award"]

STOP_WORDS = frozenset(
    {
        "the", "and", "ltd", "inc", "dba", "of", "for", "a", "an", "to", "in",
        "no", "not", "with", "by", "on", "or", "co", "corp", "company", "limited",
        "services", "service", "group", "bc", "vancouver",
    }
)

KEYWORD_EXPANSIONS: dict[str, list[str]] = {
    "building": ["construction", "build", "facility", "structure"],
    "alteration": ["renovation", "retrofit", "upgrade", "repair", "restoration"],
    "addition": ["expansion", "extension", "renovation"],
    "demolition": ["deconstruction", "removal", "abatement"],
    "concrete": ["paving", "foundation", "structural", "civil"],
    "electrical": ["electric", "lighting", "power"],
    "plumbing": ["mechanical", "hvac", "pipe"],
}


@dataclass
class CompanySignals:
    name: str
    project_types: list[str]
    neighborhoods: list[str]
    google_address: str
    avg_project_value: float
    avg_award_value: float
    award_categories: list[str]
    award_clients: list[str]
    buyer_levels: list[str]
    ai_reliability_score: int | None
    houzz_project_types: list[str] = field(default_factory=list)
    houzz_service_areas: list[str] = field(default_factory=list)
    normalized_name: str = ""

    @classmethod
    def from_company(cls, company: Company) -> CompanySignals:
        avg_award = float(company.avg_award_value or 0)
        avg_project = float(company.avg_project_value or 0)
        return cls(
            name=company.name,
            project_types=list(company.project_types or []),
            neighborhoods=list(company.neighborhoods or []),
            google_address=company.google_address or "",
            avg_project_value=avg_project,
            avg_award_value=avg_award if avg_award > 0 else avg_project,
            award_categories=list(company.award_categories or []),
            award_clients=list(company.award_clients or []),
            buyer_levels=list(company.buyer_levels or []),
            ai_reliability_score=company.ai_reliability_score,
            normalized_name=normalize_vendor_name(company.name),
        )

    @classmethod
    def from_arch_company(cls, company: ArchCompany) -> CompanySignals:
        return cls(
            name=company.name,
            project_types=list(company.project_types or []),
            neighborhoods=list(company.neighborhoods or []),
            google_address=company.google_address or "",
            avg_project_value=float(company.avg_project_value or 0),
            avg_award_value=float(company.avg_project_value or 0),
            award_categories=list(company.website_specializations or []),
            award_clients=[],
            buyer_levels=[],
            ai_reliability_score=company.ai_reliability_score,
            houzz_project_types=list(company.houzz_project_types or []),
            houzz_service_areas=list(company.houzz_service_areas or []),
            normalized_name=normalize_vendor_name(company.name),
        )


def _tokenize(text: str) -> set[str]:
    tokens = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in tokens if len(t) > 2 and t not in STOP_WORDS}


def _expand_keywords(roots: set[str]) -> set[str]:
    expanded = set(roots)
    for token in list(roots):
        for synonym in KEYWORD_EXPANSIONS.get(token, []):
            expanded.add(synonym)
    return expanded


def _company_keywords(signals: CompanySignals) -> set[str]:
    roots: set[str] = set()
    for source in (
        [signals.name],
        signals.project_types,
        signals.neighborhoods,
        [signals.google_address],
        signals.award_categories,
        signals.houzz_project_types,
        signals.houzz_service_areas,
    ):
        for text in source:
            roots.update(_tokenize(str(text)))
    return _expand_keywords(roots)


def _parse_value(raw: str | float | None) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except ValueError:
        return 0.0


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    cleaned = value.replace("/", "-").strip()[:10]
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None


def _value_fit_score(company_avg: float, opportunity_value: float) -> tuple[int, str | None]:
    if company_avg <= 0 or opportunity_value <= 0:
        return 0, None
    ratio = opportunity_value / company_avg
    if 0.5 <= ratio <= 2.0:
        return 15, "Value aligned with company typical project size"
    if 0.25 <= ratio <= 4.0:
        return 9, "Value within company broader range"
    if 0.1 <= ratio <= 10.0:
        return 3, None
    return 0, None


def _reliability_points(signals: CompanySignals, has_relevance: bool) -> int:
    if not has_relevance or signals.ai_reliability_score is None:
        return 0
    return round((signals.ai_reliability_score / 100) * 5)


def _overlap_points(haystack: str, needles: list[str], max_points: int = 20) -> tuple[int, list[str]]:
    hay_tokens = _tokenize(haystack)
    matched: list[str] = []
    for needle in needles:
        if not needle:
            continue
        needle_tokens = _tokenize(needle)
        if not needle_tokens:
            continue
        if needle_tokens & hay_tokens or needle.lower() in haystack.lower():
            matched.append(needle)
    if not matched:
        return 0, []
    return min(max_points, 8 + len(matched) * 4), matched


def _keyword_points(haystack: str, keywords: set[str]) -> tuple[int, list[str]]:
    hay_tokens = _tokenize(haystack)
    matched = sorted(hay_tokens & keywords)
    if not matched:
        return 0, []
    return min(25, 10 + len(matched) * 3), matched


def _is_tender_open(deadline: str) -> bool:
    parsed = _parse_date(deadline)
    if parsed is None:
        return True
    return parsed >= date.today()


def _score_tender(signals: CompanySignals, haystack: str, value: float, deadline: str) -> tuple[int, list[str]]:
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(haystack, signals.project_types + signals.award_categories, 18)
    loc_pts, loc_matched = _overlap_points(haystack, signals.neighborhoods + [signals.google_address], 15)
    val_pts, val_reason = _value_fit_score(signals.avg_project_value or signals.avg_award_value, value)
    has_rel = bool(kw_matched or cat_matched or loc_matched)
    rel_pts = _reliability_points(signals, has_rel)
    fresh_pts = 0
    fresh_reason = None
    parsed = _parse_date(deadline)
    if parsed and (parsed - date.today()).days <= 30:
        fresh_pts = 10
        fresh_reason = "Closing within 30 days"

    score = min(100, kw_pts + cat_pts + loc_pts + val_pts + rel_pts + fresh_pts)
    reasons: list[str] = []
    if kw_matched:
        reasons.append(f"Keyword match: {', '.join(kw_matched[:4])}")
    if cat_matched:
        reasons.append(f"Category fit: {', '.join(cat_matched[:3])}")
    if loc_matched:
        reasons.append(f"Location overlap: {', '.join(loc_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    if fresh_reason:
        reasons.append(fresh_reason)
    return score, reasons


def _score_permit(signals: CompanySignals, permit: Permit, *, own: bool) -> tuple[int, list[str]]:
    haystack = " ".join(
        filter(None, [permit.permit_type, permit.address, permit.description, permit.applicant])
    )
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(haystack, signals.project_types, 20)
    loc_pts, loc_matched = _overlap_points(haystack, signals.neighborhoods + [signals.google_address], 15)
    value = _parse_value(permit.project_value)
    val_pts, val_reason = _value_fit_score(signals.avg_project_value, value)
    base = 12 if own else 0
    score = min(100, base + kw_pts + cat_pts + loc_pts + val_pts)
    reasons: list[str] = []
    if own:
        reasons.append("Company permit history")
    else:
        reasons.append("Comparable market permit activity")
    if cat_matched:
        reasons.append(f"Permit type fit: {', '.join(cat_matched[:3])}")
    if loc_matched:
        reasons.append(f"Area overlap: {', '.join(loc_matched[:3])}")
    if kw_matched:
        reasons.append(f"Trade keyword match: {', '.join(kw_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    return score, reasons


def _score_contract_award(
    signals: CompanySignals,
    award: ContractAward,
    *,
    context: str,
) -> tuple[int, list[str]]:
    haystack = " ".join(
        filter(
            None,
            [
                award.title,
                award.description,
                award.procurement_category,
                award.buyer_organization,
                award.winner_company,
            ],
        )
    )
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(
        haystack, signals.project_types + signals.award_categories, 20
    )
    client_pts, client_matched = _overlap_points(haystack, signals.award_clients, 18)
    buyer_pts, buyer_matched = _overlap_points(haystack, signals.buyer_levels, 10)
    value = float(award.award_value or 0)
    val_pts, val_reason = _value_fit_score(signals.avg_award_value, value)
    context_bonus = {"own_history": 15, "peer_award": 8, "client_history": 12}.get(context, 5)
    score = min(100, context_bonus + kw_pts + cat_pts + client_pts + buyer_pts + val_pts)
    reasons: list[str] = []
    if context == "own_history":
        reasons.append("Company contract award history")
    elif context == "peer_award":
        reasons.append("Similar company award in same category")
    elif context == "client_history":
        reasons.append("Award from a known client/buyer")
    if cat_matched:
        reasons.append(f"Category fit: {', '.join(cat_matched[:3])}")
    if client_matched:
        reasons.append(f"Client overlap: {', '.join(client_matched[:2])}")
    if buyer_matched:
        reasons.append(f"Buyer level fit: {', '.join(buyer_matched[:2])}")
    if kw_matched:
        reasons.append(f"Keyword match: {', '.join(kw_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    return score, reasons


def _tender_payload(row: Any, source: str) -> dict[str, Any]:
    if source == "federal":
        org = row.organization
        deadline = row.closing_date
        value = _parse_value(row.estimated_value)
        budget = (row.ai_budget_estimate or "").strip() or None
    elif source == "commercial":
        org = row.company
        deadline = row.deadline
        value = _parse_value(row.value)
        budget = (row.ai_budget_estimate or "").strip() or None
    else:
        org = row.company
        deadline = row.deadline
        value = _parse_value(row.value)
        budget = (row.ai_budget_estimate or "").strip() or None

    return {
        "id": row.id,
        "title": row.title,
        "company": org or "",
        "value": value,
        "deadline": (deadline or "").replace("/", "-")[:10],
        "category": row.category or "Uncategorized",
        "budget_estimate": budget,
        "url": getattr(row, "url", "") or "",
        "tender_source": source,
    }


def _permit_payload(permit: Permit) -> dict[str, Any]:
    return {
        "id": permit.id,
        "address": permit.address,
        "type": permit.permit_type,
        "value": _parse_value(permit.project_value),
        "date": (permit.issue_date or "").replace("/", "-")[:10],
        "status": "Issued" if permit.issue_date else "Pending",
        "applicant": permit.applicant,
        "description": permit.description,
    }


def _award_payload(award: ContractAward) -> dict[str, Any]:
    return {
        "id": award.id,
        "title": award.title,
        "award_date": (award.award_date or "").replace("/", "-")[:10],
        "client": award.buyer_organization,
        "category": award.procurement_category,
        "value": float(award.award_value) if award.award_value is not None else None,
        "currency": award.currency or "CAD",
        "winner_company": award.winner_company,
        "source": award.source,
        "url": award.url,
        "buyer_level": award.buyer_level,
    }


def _load_tender_candidates(session: Session, kind: Kind, limit: int) -> list[tuple[Any, str]]:
    rows: list[tuple[Any, str]] = []
    if kind == "construction":
        federal = session.scalars(select(Tender).order_by(Tender.id.desc()).limit(limit)).all()
        commercial = session.scalars(
            select(CommercialTender).order_by(CommercialTender.id.desc()).limit(limit)
        ).all()
        rows.extend((row, "federal") for row in federal)
        rows.extend((row, "commercial") for row in commercial)
    else:
        arch = session.scalars(select(ArchTender).order_by(ArchTender.id.desc()).limit(limit)).all()
        rows.extend((row, "arch") for row in arch)
    return rows


def _load_permit_candidates(session: Session, signals: CompanySignals, limit: int) -> list[tuple[Permit, bool]]:
    results: list[tuple[Permit, bool]] = []
    seen: set[int] = set()

    if signals.normalized_name:
        own_rows = session.scalars(
            select(Permit)
            .where(Permit.applicant != "")
            .order_by(Permit.id.desc())
            .limit(limit * 3)
        ).all()
        for permit in own_rows:
            if permit.id in seen:
                continue
            if normalize_vendor_name(permit.applicant) == signals.normalized_name:
                results.append((permit, True))
                seen.add(permit.id)

    type_terms = [t.lower() for t in signals.project_types if t]
    market_query = select(Permit).order_by(Permit.id.desc()).limit(limit * 4)
    if type_terms:
        clauses = [func.lower(Permit.permit_type).contains(term) for term in type_terms[:6]]
        market_query = market_query.where(or_(*clauses))
    for permit in session.scalars(market_query).all():
        if permit.id in seen:
            continue
        own = (
            signals.normalized_name != ""
            and normalize_vendor_name(permit.applicant) == signals.normalized_name
        )
        results.append((permit, own))
        seen.add(permit.id)
        if len(results) >= limit:
            break
    return results[:limit]


def _load_award_candidates(session: Session, company: Company, limit: int) -> list[tuple[ContractAward, str]]:
    results: list[tuple[ContractAward, str]] = []
    seen: set[int] = set()

    def add(award: ContractAward, context: str) -> None:
        if award.id in seen:
            return
        results.append((award, context))
        seen.add(award.id)

    for award in session.scalars(
        select(ContractAward)
        .where(ContractAward.company_id == company.id)
        .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
        .limit(min(limit, 100))
    ).all():
        add(award, "own_history")

    categories = [c for c in (company.award_categories or []) if c]
    if categories:
        peer_ids = session.scalars(
            select(Company.id)
            .where(
                Company.id != company.id,
                Company.award_categories.op("&&")(categories),
            )
            .limit(40)
        ).all()
        if peer_ids:
            for award in session.scalars(
                select(ContractAward)
                .where(
                    ContractAward.company_id.in_(peer_ids),
                    ContractAward.company_id.isnot(None),
                )
                .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
                .limit(limit)
            ).all():
                add(award, "peer_award")

    clients = [c.strip().lower() for c in (company.award_clients or []) if c.strip()]
    if clients:
        client_rows = session.scalars(
            select(ContractAward)
            .where(ContractAward.buyer_organization != "")
            .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .limit(limit * 4)
        ).all()
        for award in client_rows:
            buyer = (award.buyer_organization or "").lower()
            if any(client in buyer or buyer in client for client in clients):
                add(award, "client_history")
            if len(results) >= limit * 2:
                break

    if len(results) < limit // 2 and categories:
        for award in session.scalars(
            select(ContractAward)
            .where(ContractAward.procurement_category.in_(categories[:10]))
            .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .limit(limit)
        ).all():
            add(award, "peer_award")

    return results[: limit * 2]


def discover_opportunities(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    min_score: int = 65,
    limit: int = 15,
    max_candidates: int = 400,
) -> dict[str, Any]:
    """Rank tenders, permits, and contract awards using internal rules only."""
    matches: list[dict[str, Any]] = []

    if kind == "architecture":
        company = session.get(ArchCompany, company_id)
        if company is None:
            raise ValueError(f"Architecture company {company_id} not found")
        signals = CompanySignals.from_arch_company(company)
        include_awards = False
    else:
        company = session.get(Company, company_id)
        if company is None:
            raise ValueError(f"Company {company_id} not found")
        signals = CompanySignals.from_company(company)
        include_awards = True

    for row, source in _load_tender_candidates(session, kind, max_candidates):
        deadline = getattr(row, "closing_date", None) or getattr(row, "deadline", "") or ""
        if not _is_tender_open(deadline):
            continue
        if kind == "architecture" and source != "arch":
            continue
        if kind == "construction" and source == "arch":
            continue
        payload = _tender_payload(row, source)
        haystack = " ".join(
            filter(
                None,
                [payload["title"], payload["category"], payload["company"], payload.get("deadline", "")],
            )
        )
        score, reasons = _score_tender(signals, haystack, payload["value"], payload["deadline"])
        if score < min_score:
            continue
        matches.append(
            {
                "type": "tender",
                "id": payload["id"],
                "score": score,
                "reasons": reasons or ["General market opportunity"],
                "source": "rules",
                "context": "open_tender",
                "payload": payload,
            }
        )

    for permit, own in _load_permit_candidates(session, signals, max_candidates // 2):
        score, reasons = _score_permit(signals, permit, own=own)
        if score < min_score:
            continue
        matches.append(
            {
                "type": "permit",
                "id": permit.id,
                "score": score,
                "reasons": reasons,
                "source": "rules",
                "context": "own_permit" if own else "market_permit",
                "payload": _permit_payload(permit),
            }
        )

    if include_awards and isinstance(company, Company):
        for award, context in _load_award_candidates(session, company, max_candidates // 2):
            score, reasons = _score_contract_award(signals, award, context=context)
            if score < min_score:
                continue
            matches.append(
                {
                    "type": "contract_award",
                    "id": award.id,
                    "score": score,
                    "reasons": reasons,
                    "source": "rules",
                    "context": context,
                    "payload": _award_payload(award),
                }
            )

    matches.sort(
        key=lambda item: (item["score"], item.get("payload", {}).get("value") or 0),
        reverse=True,
    )
    top = matches[:limit]

    return {
        "company_id": company_id,
        "kind": kind,
        "min_score": min_score,
        "limit": limit,
        "total_candidates": len(matches),
        "matches": top,
    }
