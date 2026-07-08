"""Build Company Intelligence Profile v2 from existing company data."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.company_canonical_constants import company_canonical_name
from db.models import ArchCompany, Company, ContractAward, Permit
from pipeline.business_attributes import (
    ENTITY_CLASS_MAP,
    buyer_type_from_org,
    deal_size_band,
    geographic_reach,
    infer_delivery,
    infer_orientation,
    infer_sector,
    infer_sector_from_permit,
    match_patterns,
    normalize_permit_project_type,
    parse_city_from_address,
    sector_distribution,
    specialization_confidence,
    trade_from_name,
    value_percentiles,
    DELIVERY_PATTERNS,
    SECTOR_PATTERNS,
)
from pipeline.capability_profile import (
    CCP_VERSION,
    CapabilityProfile,
    PROFILE_TTL_HOURS,
    _load_architect_partners,
    _market_segments,
    _parse_cities,
    _profile_completeness,
)
from pipeline.registry_provenance import merge_registry_provenance_into_profile
from pipeline.sector_confidence import classify_sector_confidence
from pipeline.cip_schema import CIP_VERSION, CompanyIntelligenceProfile, GeoConcentration, ProjectCluster, ValueRange
from pipeline.fit.profile_confidence import bootstrap_from_signals
from pipeline.company_matching import normalize_vendor_name
from pipeline.scoring.revenue_rank import parse_estimated_value
from pipeline.taxonomy import SOURCE_TO_SEGMENT, TRADE_PATTERNS, tag_company

Kind = Literal["construction", "architecture"]


def _load_company_permits(
    session: Session,
    normalized_name: str,
    *,
    company_id: int | None = None,
    limit: int = 500,
) -> list[Permit]:
    """Load permits for CIP sector inference.

    Prefer ``permits.company_id`` (indexed) when linked; fall back to normalized
    applicant name scan for legacy rows without company_id.
    """
    if company_id is not None:
        by_id = list(
            session.scalars(
                select(Permit)
                .where(Permit.company_id == company_id)
                .order_by(Permit.id.desc())
                .limit(limit)
            ).all()
        )
        if by_id:
            return by_id

    if not normalized_name:
        return []

    results: list[Permit] = []
    for permit in session.scalars(
        select(Permit).where(Permit.applicant != "").order_by(Permit.id.desc()).limit(limit * 4)
    ).all():
        if normalize_vendor_name(permit.applicant) == normalized_name:
            results.append(permit)
        if len(results) >= limit:
            break
    return results


def _resolve_trades(
    *,
    name: str,
    company_type: str,
    project_types: list[str],
    award_categories: list[str],
    specializations: list[str],
    permit_texts: list[str],
) -> tuple[str, list[str], list[str], float]:
    sources: list[str] = []
    counts: Counter[str] = Counter()

    name_trade = trade_from_name(name)
    if name_trade:
        counts[name_trade] += 5
        sources.append("name")

    tagged = tag_company(
        name=name,
        company_type=company_type,
        project_types=project_types,
        award_categories=award_categories,
        specializations=specializations,
    )
    counts[tagged.primary_trade] += 3
    sources.append("company_type")
    for t in tagged.secondary_trades:
        counts[t] += 1

    for text in permit_texts[:80]:
        for trade, _pattern in TRADE_PATTERNS:
            if _pattern.search(text or ""):
                counts[trade] += 1
    if permit_texts:
        sources.append("permits")

    for cat in award_categories:
        for trade, _pattern in TRADE_PATTERNS:
            if _pattern.search(cat or ""):
                counts[trade] += 2
    if award_categories:
        sources.append("awards")

    if not counts:
        primary = tagged.primary_trade or "general_building"
        return primary, [primary], sources, 0.35

    ranked = counts.most_common()
    primary = ranked[0][0]
    secondary = [t for t, _ in ranked[1:5] if t != primary]
    confidence = min(0.95, 0.45 + 0.08 * len(set(counts)) + (0.15 if "name" in sources else 0))
    return primary, secondary, sorted(set(sources)), round(confidence, 2)


def _build_clusters(
    permits: list[Permit],
    *,
    limit: int = 8,
) -> list[ProjectCluster]:
    buckets: Counter[tuple[str, str, str]] = Counter()
    values: dict[tuple[str, str, str], list[float]] = {}

    for p in permits:
        blob = f"{p.permit_type} {p.description} {p.address}"
        delivery = infer_delivery(blob, p.permit_type or "")
        sector = infer_sector_from_permit(
            p.permit_type or "",
            p.description or "",
            p.address or "",
        )
        geo = parse_city_from_address(p.address or "") or "unknown"
        key = (delivery, sector, geo)
        buckets[key] += 1
        val = parse_estimated_value(p.project_value)
        if val > 0:
            values.setdefault(key, []).append(val)

    total = sum(buckets.values()) or 1
    clusters: list[ProjectCluster] = []
    for (delivery, sector, geo), count in buckets.most_common(limit):
        avg = 0.0
        if values.get((delivery, sector, geo)):
            avg = sum(values[(delivery, sector, geo)]) / len(values[(delivery, sector, geo)])
        clusters.append(
            ProjectCluster(
                delivery=delivery,
                sector=sector,
                geo=geo,
                count=count,
                share=round(count / total, 3),
                avg_value=round(avg, 2),
            )
        )
    return clusters


def build_cip(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
) -> CompanyIntelligenceProfile:
    if kind == "architecture":
        company = session.get(ArchCompany, company_id)
        if company is None:
            raise ValueError(f"Architecture company {company_id} not found")
        canonical = company_canonical_name(company)
        normalized = normalize_vendor_name(company.name)
        permits: list[Permit] = []
        specializations = list(company.website_specializations or [])
        project_types = list(company.project_types or [])
        primary, secondary, trade_sources, trade_conf = _resolve_trades(
            name=canonical,
            company_type="Architect",
            project_types=project_types,
            award_categories=[],
            specializations=specializations + list(company.houzz_project_types or []),
            permit_texts=[],
        )
        permit_texts = [f"{s} {t}" for s in specializations for t in project_types]
        sector_focus = sector_distribution(permit_texts) or {"commercial": 0.5, "institutional": 0.5}
        delivery_types = list({infer_delivery(t) for t in permit_texts}) or ["design"]
        orientation = "design"
        award_count = 0
        award_categories: list[str] = []
        award_clients: list[str] = []
        buyer_types = ["commercial", "institutional"]
        own_permit_count = 0
        neighborhoods = list(company.neighborhoods or [])
        service_cities = _parse_cities(company.google_address, "") + list(company.houzz_service_areas or [])[:5]
        avg_val = float(company.avg_project_value or 0)
        p25, med, p75, mx = value_percentiles([avg_val] if avg_val else [])
        architect_partners: list[dict[str, Any]] = []
        entity_class = "designer"
        company_type = "Architect"
        market_segments = ["commercial", "institutional"]
    else:
        company = session.get(Company, company_id)
        if company is None:
            raise ValueError(f"Company {company_id} not found")
        canonical = company_canonical_name(company)
        normalized = normalize_vendor_name(company.name) or (company.canonical_vendor_name or "")
        permits = _load_company_permits(session, normalized, company_id=company_id)
        permit_texts = [
            f"{p.permit_type} {p.description} {p.address}" for p in permits
        ]
        project_types = list(company.project_types or [])
        award_categories = list(company.award_categories or [])
        specializations = list(company.award_categories or [])[:8]
        primary, secondary, trade_sources, trade_conf = _resolve_trades(
            name=canonical,
            company_type=company.company_type or "",
            project_types=project_types,
            award_categories=award_categories,
            specializations=specializations,
            permit_texts=permit_texts,
        )
        if permits:
            sec_counts: Counter[str] = Counter(
                infer_sector_from_permit(p.permit_type or "", p.description or "", p.address or "")
                for p in permits
            )
            total_sec = sum(sec_counts.values()) or 1
            sector_focus = {k: round(v / total_sec, 3) for k, v in sec_counts.most_common()}
        else:
            sector_focus = sector_distribution(permit_texts)
        if not sector_focus and award_categories:
            sector_focus = sector_distribution(award_categories)
        delivery_types = list({infer_delivery(t, "") for t in permit_texts[:100]})
        orientation = infer_orientation(
            permit_texts[:50]
            + award_categories
            + [a for a in award_categories]
        )
        award_count = int(company.award_count or 0)
        award_clients = list(company.award_clients or [])
        buyer_types = list({b for b in (company.buyer_levels or []) if b})
        for src in company.award_sources or []:
            seg = SOURCE_TO_SEGMENT.get((src or "").lower())
            if seg and seg not in buyer_types:
                buyer_types.append(seg)
        own_permit_count = int(company.total_projects or 0)
        neighborhoods = list(company.neighborhoods or [])
        service_cities = _parse_cities(company.google_address, company.primary_city or "")
        values = [parse_estimated_value(p.project_value) for p in permits if parse_estimated_value(p.project_value) > 0]
        if company.avg_award_value:
            values.append(float(company.avg_award_value))
        if company.avg_project_value:
            values.append(float(company.avg_project_value))
        p25, med, p75, mx = value_percentiles(values)
        avg_val = med or float(company.avg_project_value or 0)
        architect_partners = _load_architect_partners(session, normalized)
        entity_class = ENTITY_CLASS_MAP.get(company.company_type or "", "contractor")
        company_type = company.company_type or ""
        market_segments = _market_segments(list(company.buyer_levels or []), list(company.award_sources or []))

    clusters = _build_clusters(permits) if kind == "construction" else []
    city_counts: Counter[str] = Counter()
    for p in permits:
        city = parse_city_from_address(p.address or "")
        if city:
            city_counts[city] += 1
    for c in service_cities:
        city_counts[c] += 1
    total_geo = sum(city_counts.values()) or 1
    concentration = [
        GeoConcentration(geo=city, share=round(cnt / total_geo, 3), project_count=cnt)
        for city, cnt in city_counts.most_common(6)
    ]
    city_shares = {g.geo: g.share for g in concentration}
    geo_reach = geographic_reach(city_shares)

    dominant_sector = max(sector_focus, key=sector_focus.get) if sector_focus else "commercial"
    sector_confidence = (
        classify_sector_confidence(sector_focus=sector_focus, permits=permits)
        if kind == "construction"
        else ("medium" if sector_focus else "low")
    )
    normalized_pts = list(
        {
            normalize_permit_project_type(p.permit_type, p.description, p.address or "")
            for p in permits[:80]
        }
    )
    if not normalized_pts and project_types:
        normalized_pts = [normalize_permit_project_type(pt, "") for pt in project_types[:6]]

    public_count = sum(1 for b in buyer_types if b in {"federal", "provincial", "municipal"})
    public_private_ratio = round(public_count / max(len(buyer_types), 1), 2)

    growth_dirs: list[str] = []
    for sec, share in sorted(sector_focus.items(), key=lambda x: -x[1]):
        if sec != dominant_sector and share >= 0.12:
            growth_dirs.append(f"{sec}_expansion")
    for cl in clusters[1:3]:
        growth_dirs.append(f"{cl.delivery}_{cl.sector}")

    spec_conf = max(
        specialization_confidence(sector_focus, len(clusters)),
        bootstrap_from_signals(
            own_permit_count=own_permit_count,
            project_clusters=clusters,
            concentration_map=concentration,
            normalized_project_types=normalized_pts[:10],
            trade_sources=trade_sources,
            primary_trade=primary,
            sector_focus=sector_focus,
        ),
    )

    completeness = _profile_completeness(
        project_types=project_types,
        neighborhoods=neighborhoods,
        avg_project_value=avg_val,
        award_categories=award_categories,
        specializations=specializations,
        trade_confidence=trade_conf,
        kind=kind,
    )

    return CompanyIntelligenceProfile(
        version=CIP_VERSION,
        computed_at=datetime.now(timezone.utc).isoformat(),
        company_id=company_id,
        kind=kind,
        name=canonical,
        company_type=company_type,
        entity_class=entity_class,
        primary_trade=primary,
        secondary_trades=secondary,
        trade_sources=trade_sources,
        specialization_confidence=spec_conf,
        delivery_types=delivery_types[:8],
        normalized_project_types=normalized_pts[:10],
        sector_focus=sector_focus,
        dominant_sector=dominant_sector,
        sector_confidence=sector_confidence,
        work_orientation=orientation,
        buyer_types=buyer_types[:8],
        client_types=award_clients[:8],
        public_private_ratio=public_private_ratio,
        procurement_affinity="mixed" if orientation == "maintenance" else "project",
        service_cities=service_cities[:8],
        neighborhoods=neighborhoods[:8],
        concentration_map=concentration,
        geographic_reach=geo_reach,
        value_range=ValueRange(p25=p25, median=med, p75=p75, max=mx),
        typical_project_value=avg_val,
        deal_size_band=deal_size_band(med),
        project_clusters=clusters,
        own_permit_count=own_permit_count,
        award_count=award_count,
        award_categories=award_categories[:10],
        award_clients=award_clients[:10],
        architect_partners=architect_partners,
        repeat_clients=award_clients[:8],
        growth_direction=growth_dirs[:6],
        expansion_confidence=round(min(0.8, len(growth_dirs) * 0.15), 2),
        profile_completeness=completeness,
        normalized_name=normalized,
        market_segments=market_segments,
        specializations=specializations,
    )


def cip_to_capability_profile(cip: CompanyIntelligenceProfile) -> CapabilityProfile:
    """Map CIP v2 → legacy CapabilityProfile for transitional code paths."""
    return CapabilityProfile(
        version=CCP_VERSION,
        computed_at=cip.computed_at,
        company_id=cip.company_id,
        kind=cip.kind,
        name=cip.name,
        company_type=cip.company_type,
        primary_trade=cip.primary_trade,
        trade_tags=[cip.primary_trade, *cip.secondary_trades],
        trade_confidence=cip.specialization_confidence,
        project_types=cip.normalized_project_types or cip.delivery_types,
        project_type_distribution=cip.sector_focus,
        neighborhoods=cip.neighborhoods,
        service_cities=cip.service_cities,
        avg_project_value=cip.typical_project_value,
        avg_award_value=cip.value_range.median or cip.typical_project_value,
        award_count=cip.award_count,
        award_categories=cip.award_categories,
        award_clients=cip.award_clients,
        buyer_levels=cip.buyer_types,
        market_segments=cip.market_segments,
        own_permit_count=cip.own_permit_count,
        architect_partners=cip.architect_partners,
        repeat_clients=cip.repeat_clients,
        specializations=cip.specializations,
        profile_completeness=cip.profile_completeness,
        normalized_name=cip.normalized_name,
    )


def persist_cip(session: Session, cip: CompanyIntelligenceProfile) -> None:
    now = datetime.now(timezone.utc)
    payload = cip.to_dict()
    legacy = cip_to_capability_profile(cip).to_dict()

    if cip.kind == "architecture":
        row = session.get(ArchCompany, cip.company_id)
        if row is None:
            return
        row.primary_trade = cip.primary_trade
        row.trade_tags = [cip.primary_trade, *cip.secondary_trades[:4]]
        row.capability_profile_json = merge_registry_provenance_into_profile(
            legacy,
            existing_capability_profile_json=row.capability_profile_json if isinstance(row.capability_profile_json, dict) else None,
        )
        row.capability_profile_at = now
        row.cip_json = payload
        row.cip_at = now
        row.cip_version = CIP_VERSION
        row.dominant_sector = cip.dominant_sector
        row.work_orientation = cip.work_orientation
        row.specialization_confidence = cip.specialization_confidence
        row.geographic_reach = cip.geographic_reach
        row.value_p25 = cip.value_range.p25
        row.value_p75 = cip.value_range.p75
    else:
        row = session.get(Company, cip.company_id)
        if row is None:
            return
        row.primary_trade = cip.primary_trade
        row.trade_tags = [cip.primary_trade, *cip.secondary_trades[:4]]
        row.capability_profile_json = merge_registry_provenance_into_profile(
            legacy,
            existing_capability_profile_json=row.capability_profile_json if isinstance(row.capability_profile_json, dict) else None,
        )
        row.capability_profile_at = now
        row.cip_json = payload
        row.cip_at = now
        row.cip_version = CIP_VERSION
        row.dominant_sector = cip.dominant_sector
        row.sector_confidence = cip.sector_confidence
        row.work_orientation = cip.work_orientation
        row.specialization_confidence = cip.specialization_confidence
        row.geographic_reach = cip.geographic_reach
        row.value_p25 = cip.value_range.p25
        row.value_p75 = cip.value_range.p75
    session.commit()


def get_cip(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    refresh: bool = False,
) -> CompanyIntelligenceProfile:
    model = ArchCompany if kind == "architecture" else Company
    company = session.get(model, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")

    stale = True
    cip_at = getattr(company, "cip_at", None)
    cip_json = getattr(company, "cip_json", None)
    if cip_at and cip_json and not refresh:
        age = datetime.now(timezone.utc) - cip_at.replace(tzinfo=timezone.utc)
        stale = age.total_seconds() > PROFILE_TTL_HOURS * 3600

    if not stale and cip_json:
        return CompanyIntelligenceProfile.from_dict(cip_json)

    cip = build_cip(session, company_id=company_id, kind=kind)
    persist_cip(session, cip)
    return cip


def get_capability_profile_from_cip(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    refresh: bool = False,
) -> CapabilityProfile:
    cip = get_cip(session, company_id=company_id, kind=kind, refresh=refresh)
    return cip_to_capability_profile(cip)
