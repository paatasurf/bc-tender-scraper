"""Match canonical companies to ODB reference records (T1–T5)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.company_canonical_constants import ENTITY_ROLE_CANONICAL
from db.market_registry_constants import OBSERVATION_STATUS_ACTIVE
from db.models import Company, CompanyRegistryLink, OdbusReference
from db.registry_constants import (
    MATCH_TIER_CONFIDENCE,
    MATCH_TIER_T1,
    MATCH_TIER_T2,
    MATCH_TIER_T3,
    MATCH_TIER_T4,
    MATCH_TIER_T5,
    REGISTRY_SOURCE_ODBUS,
    VERIFICATION_CONFIRMED_ACTIVE,
    VERIFICATION_CONFIRMED_INACTIVE,
    VERIFICATION_REVIEW_PENDING,
)
from pipeline.company_resolution import MIN_DBA_FAMILY_PREFIX_LEN
from pipeline.registry_verification.city_normalize import normalize_city
from pipeline.registry_verification.match_common import company_normalized_name, resolve_company_city

__all__ = [
    "company_normalized_name",
    "resolve_company_city",
    "OdbusMatchResult",
    "OdbusMatchIndex",
    "match_company_to_odbus",
    "match_odbus_for_companies",
    "build_odbus_link_metadata",
]

BC_PROVINCE = "BC"
FAMILY_PREFIX_LEN = MIN_DBA_FAMILY_PREFIX_LEN
FUZZY_TOKEN_THRESHOLD = 0.85


@dataclass
class OdbusMatchResult:
    reference: OdbusReference
    match_tier: str
    confidence: float
    verification_status: str
    multi_location: bool = False
    multi_location_cities: list[str] | None = None


def _verification_status(odbus_status: str, match_tier: str) -> str:
    if match_tier in {MATCH_TIER_T4, MATCH_TIER_T5}:
        return VERIFICATION_REVIEW_PENDING
    if (odbus_status or "").strip().lower() == "active":
        return VERIFICATION_CONFIRMED_ACTIVE
    return VERIFICATION_CONFIRMED_INACTIVE


def _naics_display(reference: OdbusReference) -> str:
    if reference.source_naics and len(reference.source_naics) >= 4:
        return reference.source_naics
    return reference.derived_naics


def build_odbus_link_metadata(
    reference: OdbusReference,
    *,
    multi_location_cities: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "business_name": reference.business_name,
        "status": reference.status,
        "naics": _naics_display(reference),
        "derived_naics": reference.derived_naics,
        "source_naics": reference.source_naics,
        "provider": reference.provider,
        "licence_number": reference.licence_number,
        "city": reference.city,
        "province": reference.province,
    }
    if multi_location_cities:
        payload["multi_location_cities"] = multi_location_cities
    return payload


def _prefer_active(records: list[OdbusReference]) -> OdbusReference:
    for record in records:
        if (record.status or "").strip().lower() == "active":
            return record
    return records[0]


def _bc_records(records: list[OdbusReference]) -> list[OdbusReference]:
    return [record for record in records if (record.province or "").upper() == BC_PROVINCE]


def _distinct_cities(records: list[OdbusReference]) -> list[str]:
    cities = sorted({record.city for record in records if record.city})
    return cities


def _token_set(name: str) -> set[str]:
    import re

    tokens = re.findall(r"[a-z0-9]{3,}", name.lower())
    return set(tokens)


def _token_similarity(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(len(left_tokens), len(right_tokens))


class OdbusMatchIndex:
    def __init__(self, references: list[OdbusReference]) -> None:
        self.by_name_city: dict[tuple[str, str], list[OdbusReference]] = defaultdict(list)
        self.by_name: dict[str, list[OdbusReference]] = defaultdict(list)
        for reference in references:
            key = (reference.normalized_name, reference.normalized_city)
            self.by_name_city[key].append(reference)
            self.by_name[reference.normalized_name].append(reference)


def build_odbus_match_index(session: Session) -> OdbusMatchIndex:
    references = session.scalars(
        select(OdbusReference).where(OdbusReference.observation_status == OBSERVATION_STATUS_ACTIVE)
    ).all()
    return OdbusMatchIndex(list(references))


def match_company_to_odbus(
    company: Company,
    index: OdbusMatchIndex,
    *,
    include_review_tiers: bool = False,
) -> OdbusMatchResult | None:
    normalized_name = company_normalized_name(company)
    if not normalized_name:
        return None

    company_city = resolve_company_city(company)

    if company_city:
        t1_records = _bc_records(index.by_name_city.get((normalized_name, company_city), []))
        if t1_records:
            reference = _prefer_active(t1_records)
            tier = MATCH_TIER_T1
            return OdbusMatchResult(
                reference=reference,
                match_tier=tier,
                confidence=MATCH_TIER_CONFIDENCE[tier],
                verification_status=_verification_status(reference.status, tier),
            )

    name_records = _bc_records(index.by_name.get(normalized_name, []))
    if name_records:
        cities = _distinct_cities(name_records)
        if len(cities) == 1:
            city_records = [record for record in name_records if record.normalized_city == normalize_city(cities[0])]
            reference = _prefer_active(city_records or name_records)
            tier = MATCH_TIER_T2
            return OdbusMatchResult(
                reference=reference,
                match_tier=tier,
                confidence=MATCH_TIER_CONFIDENCE[tier],
                verification_status=_verification_status(reference.status, tier),
            )
        if len(cities) > 1:
            if company_city:
                city_records = [record for record in name_records if record.normalized_city == company_city]
                reference = _prefer_active(city_records or name_records)
            else:
                reference = _prefer_active(name_records)
            tier = MATCH_TIER_T3
            return OdbusMatchResult(
                reference=reference,
                match_tier=tier,
                confidence=MATCH_TIER_CONFIDENCE[tier],
                verification_status=_verification_status(reference.status, tier),
                multi_location=True,
                multi_location_cities=cities,
            )

    if not include_review_tiers:
        return None

    family_candidates: list[OdbusReference] = []
    if len(normalized_name) >= FAMILY_PREFIX_LEN:
        prefix = normalized_name[:FAMILY_PREFIX_LEN]
        for odb_name, records in index.by_name.items():
            if odb_name == normalized_name:
                continue
            if len(odb_name) >= FAMILY_PREFIX_LEN and odb_name[:FAMILY_PREFIX_LEN] == prefix:
                family_candidates.extend(_bc_records(records))
    if family_candidates:
        reference = _prefer_active(family_candidates)
        tier = MATCH_TIER_T4
        return OdbusMatchResult(
            reference=reference,
            match_tier=tier,
            confidence=MATCH_TIER_CONFIDENCE[tier],
            verification_status=_verification_status(reference.status, tier),
        )

    fuzzy_candidates: list[tuple[float, OdbusReference]] = []
    for odb_name, records in index.by_name.items():
        if odb_name == normalized_name:
            continue
        score = _token_similarity(normalized_name, odb_name)
        if score >= FUZZY_TOKEN_THRESHOLD:
            for record in _bc_records(records):
                fuzzy_candidates.append((score, record))
    if fuzzy_candidates:
        fuzzy_candidates.sort(key=lambda item: item[0], reverse=True)
        reference = fuzzy_candidates[0][1]
        tier = MATCH_TIER_T5
        return OdbusMatchResult(
            reference=reference,
            match_tier=tier,
            confidence=MATCH_TIER_CONFIDENCE[tier],
            verification_status=_verification_status(reference.status, tier),
        )

    return None


def _persist_link(session: Session, company_id: int, match: OdbusMatchResult) -> CompanyRegistryLink | None:
    existing = session.scalar(
        select(CompanyRegistryLink.id).where(
            CompanyRegistryLink.source == REGISTRY_SOURCE_ODBUS,
            CompanyRegistryLink.external_id == match.reference.odbus_idx,
        )
    )
    if existing is not None:
        return None

    metadata = build_odbus_link_metadata(
        match.reference,
        multi_location_cities=match.multi_location_cities,
    )
    link = CompanyRegistryLink(
        company_id=company_id,
        source=REGISTRY_SOURCE_ODBUS,
        external_id=match.reference.odbus_idx,
        match_tier=match.match_tier,
        confidence=match.confidence,
        verification_status=match.verification_status,
        multi_location=match.multi_location,
        metadata_json=metadata,
        linked_at=datetime.now(timezone.utc),
    )
    session.add(link)
    return link


def match_odbus_for_companies(
    session: Session,
    *,
    company_ids: list[int] | None = None,
    include_review_tiers: bool = False,
) -> dict[str, Any]:
    """Match canonical companies to ODB records. Never creates company rows."""
    query = select(Company).where(Company.entity_role == ENTITY_ROLE_CANONICAL)
    if company_ids:
        query = query.where(Company.id.in_(company_ids))
    companies = session.scalars(query).all()

    if not companies:
        return {
            "source": REGISTRY_SOURCE_ODBUS,
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
            CompanyRegistryLink.source == REGISTRY_SOURCE_ODBUS,
        )
    )

    index = build_odbus_match_index(session)
    by_tier: dict[str, int] = defaultdict(int)
    matched = 0
    links_created = 0

    for company in companies:
        match = match_company_to_odbus(company, index, include_review_tiers=include_review_tiers)
        if match is None:
            continue
        matched += 1
        by_tier[match.match_tier] += 1
        link = _persist_link(session, company.id, match)
        if link is not None:
            links_created += 1

    session.commit()

    return {
        "source": REGISTRY_SOURCE_ODBUS,
        "companies_processed": len(companies),
        "links_created": links_created,
        "matched": matched,
        "unmatched": len(companies) - matched,
        "by_tier": dict(by_tier),
        "include_review_tiers": include_review_tiers,
    }
