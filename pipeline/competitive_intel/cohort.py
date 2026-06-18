"""Market cohort construction and peer candidate filtering."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.models import ArchCompany, Company
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.competitive_intel.overlap import city_set, shares_geography
from pipeline.competitive_intel.types import CompanyRow, Kind, MarketCohort


def _model_for_kind(kind: Kind):
    return ArchCompany if kind == "architecture" else Company


def subject_city(subject: CompanyRow, cip: CompanyIntelligenceProfile, kind: Kind) -> str:
    if kind == "construction":
        return (getattr(subject, "primary_city", "") or "").strip()
    if cip.service_cities:
        return (cip.service_cities[0] or "").strip()
    for area in getattr(subject, "website_service_areas", None) or []:
        if area:
            return area.strip()
    return ""


def _quality_clause(model, kind: Kind):
    if kind == "construction":
        return or_(model.total_projects >= 2, model.award_count >= 1)
    return model.total_projects >= 2


def _sector_clause(model, subject: CompanyRow):
    sector = (subject.dominant_sector or "").strip()
    trade = (subject.primary_trade or "").strip()
    if sector and trade:
        return or_(model.dominant_sector == sector, model.primary_trade == trade)
    if sector:
        return model.dominant_sector == sector
    if trade:
        return model.primary_trade == trade
    return True


def _fetch_cohort_rows(
    session: Session,
    *,
    subject: CompanyRow,
    kind: Kind,
    use_city: bool,
    city: str,
    limit: int = 500,
) -> list[CompanyRow]:
    model = _model_for_kind(kind)
    query = select(model).where(model.id != subject.id)
    sector_filter = _sector_clause(model, subject)
    if sector_filter is not True:
        query = query.where(sector_filter)
    query = query.where(_quality_clause(model, kind))

    if use_city and city and kind == "construction":
        query = query.where(model.primary_city.ilike(city))

    return list(session.scalars(query.limit(limit)).all())


def _filter_arch_city(
    members: list[CompanyRow],
    subject: CompanyRow,
    subject_cip: CompanyIntelligenceProfile,
    city: str,
) -> list[CompanyRow]:
    if not city:
        return members
    city_l = city.strip().lower()
    filtered: list[CompanyRow] = []
    for member in members:
        areas = [a.lower() for a in (getattr(member, "website_service_areas", None) or []) if a]
        if any(city_l in a or a in city_l for a in areas):
            filtered.append(member)
            continue
        addr = (getattr(member, "google_address", "") or "").lower()
        if city_l in addr:
            filtered.append(member)
    return filtered if filtered else members


def build_market_cohort(
    session: Session,
    subject: CompanyRow,
    subject_cip: CompanyIntelligenceProfile,
    *,
    kind: Kind,
) -> MarketCohort:
    city = subject_city(subject, subject_cip, kind)
    sector = (subject.dominant_sector or subject.primary_trade or "general").strip()

    members = _fetch_cohort_rows(session, subject=subject, kind=kind, use_city=True, city=city)
    if kind == "architecture" and city:
        members = _filter_arch_city(members, subject, subject_cip, city)

    definition_key = "sector_and_city"
    if city:
        definition = f"dominant_sector={sector}, city={city}"
    else:
        definition = f"dominant_sector={sector}"

    if len(members) < 8:
        members = _fetch_cohort_rows(session, subject=subject, kind=kind, use_city=False, city=city)
        definition_key = "sector_only_widened"
        definition = f"dominant_sector={sector} (widened — cohort < 8 with city gate)"

    return MarketCohort(
        members=members,
        definition=definition,
        definition_key=definition_key,
        cohort_size=len(members),
    )


def filter_peer_candidates(
    cohort: MarketCohort,
    *,
    subject_id: int,
    subject_cip: CompanyIntelligenceProfile,
    subject: CompanyRow,
    peer_cips: dict[int, CompanyIntelligenceProfile],
    apply_geo_gate: bool,
) -> list[CompanyRow]:
    candidates: list[CompanyRow] = []
    for member in cohort.members:
        if member.id == subject_id:
            continue
        peer_cip = peer_cips.get(member.id)
        if peer_cip is None:
            continue
        if apply_geo_gate and cohort.definition_key == "sector_only_widened":
            if not shares_geography(subject_cip, peer_cip, subject, member):
                continue
        candidates.append(member)
    return candidates
