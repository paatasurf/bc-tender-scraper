"""Company Capability Profile (CCP) — understand the company before filtering the market."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchCompany, Company, ContractAward, Permit
from pipeline.company_matching import normalize_vendor_name
from pipeline.taxonomy import SOURCE_TO_SEGMENT, tag_company

Kind = Literal["construction", "architecture"]
CCP_VERSION = 1
PROFILE_TTL_HOURS = 24


@dataclass
class CapabilityProfile:
    version: int
    computed_at: str
    company_id: int
    kind: Kind
    name: str
    company_type: str
    primary_trade: str
    trade_tags: list[str]
    trade_confidence: float
    project_types: list[str]
    project_type_distribution: dict[str, float]
    neighborhoods: list[str]
    service_cities: list[str]
    avg_project_value: float
    avg_award_value: float
    award_count: int
    award_categories: list[str]
    award_clients: list[str]
    buyer_levels: list[str]
    market_segments: list[str]
    own_permit_count: int
    architect_partners: list[dict[str, Any]] = field(default_factory=list)
    repeat_clients: list[str] = field(default_factory=list)
    specializations: list[str] = field(default_factory=list)
    profile_completeness: float = 0.0
    normalized_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityProfile:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _distribution(items: list[str]) -> dict[str, float]:
    if not items:
        return {}
    counts = Counter(items)
    total = sum(counts.values())
    return {k: round(v / total, 3) for k, v in counts.most_common(10)}


def _parse_cities(address: str, primary_city: str) -> list[str]:
    cities: list[str] = []
    if primary_city:
        cities.append(primary_city.strip())
    if address:
        parts = [p.strip() for p in address.split(",")]
        for part in parts:
            lower = part.lower()
            if lower in {"bc", "british columbia", "canada", "vancouver"}:
                continue
            if part and part not in cities and len(part) < 60:
                cities.append(part)
    return cities[:8]


def _market_segments(buyer_levels: list[str], award_sources: list[str]) -> list[str]:
    segments: set[str] = set()
    for level in buyer_levels:
        cleaned = level.strip().lower()
        if cleaned:
            segments.add(cleaned)
    for source in award_sources:
        seg = SOURCE_TO_SEGMENT.get(source.lower())
        if seg:
            segments.add(seg)
    return sorted(segments)


def _load_architect_partners(session: Session, normalized_name: str, limit: int = 5) -> list[dict[str, Any]]:
    if not normalized_name:
        return []
    counts: Counter[str] = Counter()
    for permit in session.scalars(
        select(Permit)
        .where(Permit.applicant != "", Permit.architect != "")
        .order_by(Permit.id.desc())
        .limit(8000)
    ).all():
        if normalize_vendor_name(permit.applicant) != normalized_name:
            continue
        architect = (permit.architect or "").strip()
        if architect:
            counts[architect] += 1
    return [{"name": name, "project_count": count} for name, count in counts.most_common(limit)]


def _profile_completeness(**kwargs: Any) -> float:
    checks = [
        bool(kwargs.get("project_types")),
        bool(kwargs.get("neighborhoods")),
        kwargs.get("avg_project_value", 0) > 0,
        bool(kwargs.get("award_categories")) or kwargs.get("kind") == "architecture",
        bool(kwargs.get("specializations")) or kwargs.get("kind") == "construction",
        kwargs.get("trade_confidence", 0) >= 0.5,
    ]
    return round(sum(1 for c in checks if c) / len(checks), 2)


def build_capability_profile(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
) -> CapabilityProfile:
    if kind == "architecture":
        company = session.get(ArchCompany, company_id)
        if company is None:
            raise ValueError(f"Architecture company {company_id} not found")
        trade = tag_company(
            name=company.name,
            company_type="Architect",
            project_types=list(company.project_types or []),
            specializations=list(company.website_specializations or []) + list(company.houzz_project_types or []),
        )
        normalized = normalize_vendor_name(company.name)
        profile = CapabilityProfile(
            version=CCP_VERSION,
            computed_at=datetime.now(timezone.utc).isoformat(),
            company_id=company_id,
            kind=kind,
            name=company.name,
            company_type="Architect",
            primary_trade=trade.primary_trade,
            trade_tags=trade.all_tags,
            trade_confidence=trade.confidence,
            project_types=list(company.project_types or []),
            project_type_distribution=_distribution(list(company.project_types or [])),
            neighborhoods=list(company.neighborhoods or []),
            service_cities=_parse_cities(company.google_address, "") + list(company.houzz_service_areas or [])[:5],
            avg_project_value=float(company.avg_project_value or 0),
            avg_award_value=float(company.avg_project_value or 0),
            award_count=0,
            award_categories=[],
            award_clients=[],
            buyer_levels=[],
            market_segments=["commercial", "institutional"],
            own_permit_count=0,
            architect_partners=[],
            repeat_clients=[],
            specializations=list(company.website_specializations or []),
            normalized_name=normalized,
        )
    else:
        company = session.get(Company, company_id)
        if company is None:
            raise ValueError(f"Company {company_id} not found")
        trade = tag_company(
            name=company.name,
            company_type=company.company_type or "",
            project_types=list(company.project_types or []),
            award_categories=list(company.award_categories or []),
        )
        normalized = normalize_vendor_name(company.name) or (company.canonical_vendor_name or "")
        profile = CapabilityProfile(
            version=CCP_VERSION,
            computed_at=datetime.now(timezone.utc).isoformat(),
            company_id=company_id,
            kind=kind,
            name=company.name,
            company_type=company.company_type or "",
            primary_trade=trade.primary_trade,
            trade_tags=trade.all_tags,
            trade_confidence=trade.confidence,
            project_types=list(company.project_types or []),
            project_type_distribution=_distribution(list(company.project_types or [])),
            neighborhoods=list(company.neighborhoods or []),
            service_cities=_parse_cities(company.google_address, company.primary_city or ""),
            avg_project_value=float(company.avg_project_value or 0),
            avg_award_value=float(company.avg_award_value or company.avg_project_value or 0),
            award_count=int(company.award_count or 0),
            award_categories=list(company.award_categories or []),
            award_clients=list(company.award_clients or []),
            buyer_levels=list(company.buyer_levels or []),
            market_segments=_market_segments(
                list(company.buyer_levels or []),
                list(company.award_sources or []),
            ),
            own_permit_count=int(company.total_projects or 0),
            architect_partners=_load_architect_partners(session, normalized),
            repeat_clients=list(company.award_clients or [])[:8],
            specializations=list(company.award_categories or [])[:8],
            normalized_name=normalized,
        )

    profile.profile_completeness = _profile_completeness(
        project_types=profile.project_types,
        neighborhoods=profile.neighborhoods,
        avg_project_value=profile.avg_project_value,
        award_categories=profile.award_categories,
        specializations=profile.specializations,
        trade_confidence=profile.trade_confidence,
        kind=kind,
    )
    return profile


def persist_capability_profile(session: Session, profile: CapabilityProfile) -> None:
    now = datetime.now(timezone.utc)
    payload = profile.to_dict()
    if profile.kind == "architecture":
        row = session.get(ArchCompany, profile.company_id)
        if row is None:
            return
        row.primary_trade = profile.primary_trade
        row.trade_tags = profile.trade_tags
        row.capability_profile_json = payload
        row.capability_profile_at = now
    else:
        row = session.get(Company, profile.company_id)
        if row is None:
            return
        row.primary_trade = profile.primary_trade
        row.trade_tags = profile.trade_tags
        row.capability_profile_json = payload
        row.capability_profile_at = now
    session.commit()


def get_capability_profile(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    refresh: bool = False,
) -> CapabilityProfile:
    from pipeline.cip_builder import get_capability_profile_from_cip

    return get_capability_profile_from_cip(
        session, company_id=company_id, kind=kind, refresh=refresh
    )
