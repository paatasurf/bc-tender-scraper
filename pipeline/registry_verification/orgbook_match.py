"""Match canonical companies to OrgBook reference records (T1–T3)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.company_canonical_constants import ENTITY_ROLE_CANONICAL
from db.models import Company, CompanyRegistryLink, OrgbookReference
from db.registry_constants import (
    MATCH_TIER_CONFIDENCE,
    MATCH_TIER_T1,
    MATCH_TIER_T2,
    MATCH_TIER_T3,
    REGISTRY_SOURCE_ORGBOOK,
    VERIFICATION_CONFIRMED_ACTIVE,
    VERIFICATION_CONFIRMED_INACTIVE,
)
from pipeline.registry_verification.match_common import company_normalized_name, resolve_company_city

BC_PROVINCE = "BC"


@dataclass
class OrgbookMatchResult:
    reference: OrgbookReference
    match_tier: str
    confidence: float
    verification_status: str
    multi_location: bool = False
    multi_location_cities: list[str] | None = None


def _verification_status(orgbook_status: str) -> str:
    status = (orgbook_status or "").strip().lower()
    if status in {"active", "registered"}:
        return VERIFICATION_CONFIRMED_ACTIVE
    return VERIFICATION_CONFIRMED_INACTIVE


def build_orgbook_link_metadata(
    reference: OrgbookReference,
    *,
    multi_location_cities: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "business_name": reference.legal_name,
        "legal_name": reference.legal_name,
        "dba_names": reference.dba_names or [],
        "status": reference.status,
        "business_number": reference.business_number,
        "registry_id": reference.registry_id,
        "entity_type": reference.entity_type,
        "city": reference.city,
        "province": reference.province,
        "provider": "BC OrgBook",
    }
    if multi_location_cities:
        payload["multi_location_cities"] = multi_location_cities
    if reference.metadata_json:
        payload["provider_metadata"] = reference.metadata_json
    return payload


def _prefer_active(records: list[OrgbookReference]) -> OrgbookReference:
    for record in records:
        if (record.status or "").strip().lower() in {"active", "registered"}:
            return record
    return records[0]


def _bc_records(records: list[OrgbookReference]) -> list[OrgbookReference]:
    return [record for record in records if (record.province or BC_PROVINCE).upper() == BC_PROVINCE]


def _distinct_cities(records: list[OrgbookReference]) -> list[str]:
    return sorted({record.city for record in records if record.city})


class OrgbookMatchIndex:
    def __init__(self, references: list[OrgbookReference]) -> None:
        self.by_name_city: dict[tuple[str, str], list[OrgbookReference]] = defaultdict(list)
        self.by_name: dict[str, list[OrgbookReference]] = defaultdict(list)
        for reference in references:
            key = (reference.normalized_name, reference.normalized_city)
            self.by_name_city[key].append(reference)
            self.by_name[reference.normalized_name].append(reference)


def build_orgbook_match_index(session: Session) -> OrgbookMatchIndex:
    references = session.scalars(select(OrgbookReference)).all()
    return OrgbookMatchIndex(list(references))


def match_company_to_orgbook(
    company: Company,
    index: OrgbookMatchIndex,
) -> OrgbookMatchResult | None:
    """Deterministic OrgBook matching: T1 name+city, T2 single city, T3 multi-city."""
    normalized_name = company_normalized_name(company)
    if not normalized_name:
        return None

    company_city = resolve_company_city(company)

    if company_city:
        t1_records = _bc_records(index.by_name_city.get((normalized_name, company_city), []))
        if t1_records:
            reference = _prefer_active(t1_records)
            tier = MATCH_TIER_T1
            return OrgbookMatchResult(
                reference=reference,
                match_tier=tier,
                confidence=MATCH_TIER_CONFIDENCE[tier],
                verification_status=_verification_status(reference.status),
            )

    name_records = _bc_records(index.by_name.get(normalized_name, []))
    if name_records:
        cities = _distinct_cities(name_records)
        if len(cities) == 1:
            from pipeline.registry_verification.city_normalize import normalize_city

            city_records = [
                record for record in name_records if record.normalized_city == normalize_city(cities[0])
            ]
            reference = _prefer_active(city_records or name_records)
            tier = MATCH_TIER_T2
            return OrgbookMatchResult(
                reference=reference,
                match_tier=tier,
                confidence=MATCH_TIER_CONFIDENCE[tier],
                verification_status=_verification_status(reference.status),
            )
        if len(cities) > 1:
            if company_city:
                city_records = [record for record in name_records if record.normalized_city == company_city]
                reference = _prefer_active(city_records or name_records)
            else:
                reference = _prefer_active(name_records)
            tier = MATCH_TIER_T3
            return OrgbookMatchResult(
                reference=reference,
                match_tier=tier,
                confidence=MATCH_TIER_CONFIDENCE[tier],
                verification_status=_verification_status(reference.status),
                multi_location=True,
                multi_location_cities=cities,
            )

    return None


def _persist_link(session: Session, company_id: int, match: OrgbookMatchResult) -> CompanyRegistryLink | None:
    existing = session.scalar(
        select(CompanyRegistryLink.id).where(
            CompanyRegistryLink.source == REGISTRY_SOURCE_ORGBOOK,
            CompanyRegistryLink.external_id == match.reference.orgbook_id,
        )
    )
    if existing is not None:
        return None

    metadata = build_orgbook_link_metadata(
        match.reference,
        multi_location_cities=match.multi_location_cities,
    )
    link = CompanyRegistryLink(
        company_id=company_id,
        source=REGISTRY_SOURCE_ORGBOOK,
        external_id=match.reference.orgbook_id,
        match_tier=match.match_tier,
        confidence=match.confidence,
        verification_status=match.verification_status,
        multi_location=match.multi_location,
        metadata_json=metadata,
        linked_at=datetime.now(timezone.utc),
    )
    session.add(link)
    return link


def match_orgbook_for_companies(
    session: Session,
    *,
    company_ids: list[int] | None = None,
    include_review_tiers: bool = False,
) -> dict[str, Any]:
    """Match canonical companies to OrgBook records. Never creates company rows."""
    del include_review_tiers  # OrgBook uses deterministic T1–T3 only

    query = select(Company).where(Company.entity_role == ENTITY_ROLE_CANONICAL)
    if company_ids:
        query = query.where(Company.id.in_(company_ids))
    companies = session.scalars(query).all()

    if not companies:
        return {
            "source": REGISTRY_SOURCE_ORGBOOK,
            "companies_processed": 0,
            "links_created": 0,
            "matched": 0,
            "unmatched": 0,
            "by_tier": {},
        }

    target_ids = [company.id for company in companies]
    session.execute(
        delete(CompanyRegistryLink).where(
            CompanyRegistryLink.company_id.in_(target_ids),
            CompanyRegistryLink.source == REGISTRY_SOURCE_ORGBOOK,
        )
    )

    index = build_orgbook_match_index(session)
    by_tier: dict[str, int] = defaultdict(int)
    matched = 0
    links_created = 0

    for company in companies:
        match = match_company_to_orgbook(company, index)
        if match is None:
            continue
        matched += 1
        by_tier[match.match_tier] += 1
        link = _persist_link(session, company.id, match)
        if link is not None:
            links_created += 1

    session.commit()

    return {
        "source": REGISTRY_SOURCE_ORGBOOK,
        "companies_processed": len(companies),
        "links_created": links_created,
        "matched": matched,
        "unmatched": len(companies) - matched,
        "by_tier": dict(by_tier),
    }
