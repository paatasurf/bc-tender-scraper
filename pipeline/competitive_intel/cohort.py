"""Market cohort construction and peer candidate filtering."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.company_analytics import company_analytics_entity_filter
from db.company_canonical_constants import COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES
from db.models import ArchCompany, Company
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.competitive_intel.cohort_isolation import apply_cohort_type_isolation
from pipeline.competitive_intel.overlap import city_set, shares_geography
from pipeline.competitive_intel.types import CompanyRow, Kind, MarketCohort


def _model_for_kind(kind: Kind):
    return ArchCompany if kind == "architecture" else Company


def subject_city(
    subject: CompanyRow, cip: CompanyIntelligenceProfile, kind: Kind
) -> str:
    if kind == "construction":
        return (getattr(subject, "primary_city", "") or "").strip()
    if cip.service_cities:
        return (cip.service_cities[0] or "").strip()
    for area in getattr(subject, "website_service_areas", None) or []:
        if area:
            return area.strip()
    return ""


def _normalize_token(value: str) -> str:
    return (value or "").strip().lower()


def _peer_matches_sector_or_trade(subject: CompanyRow, member: CompanyRow) -> bool:
    sector = (subject.dominant_sector or "").strip()
    trade = (subject.primary_trade or "").strip()
    member_sector = (getattr(member, "dominant_sector", "") or "").strip()
    member_trade = (getattr(member, "primary_trade", "") or "").strip()
    if sector and member_sector == sector:
        return True
    if trade and member_trade == trade:
        return True
    return not sector and not trade


def _award_category_overlap_ratio(subject: CompanyRow, member: CompanyRow) -> float:
    subject_types = {
        _normalize_token(t)
        for t in (getattr(subject, "project_types", None) or [])
        if t
    }
    peer_categories = {
        _normalize_token(t)
        for t in (getattr(member, "award_categories", None) or [])
        if t
    }
    if not subject_types or not peer_categories:
        return 0.0
    union = subject_types | peer_categories
    if not union:
        return 0.0
    return len(subject_types & peer_categories) / len(union)


def _passes_cohort_quality_gate(
    subject: CompanyRow, member: CompanyRow, *, kind: Kind
) -> bool:
    if kind != "construction":
        return True
    if not _peer_matches_sector_or_trade(subject, member):
        return False

    subject_projects = int(getattr(subject, "total_projects", 0) or 0)
    peer_projects = int(getattr(member, "total_projects", 0) or 0)

    if peer_projects == 0:
        return _award_category_overlap_ratio(subject, member) > 0.5

    if subject_projects >= 10 and peer_projects < 2:
        return False

    return True


def _apply_cohort_quality_gate(
    members: list[CompanyRow],
    subject: CompanyRow,
    *,
    kind: Kind,
) -> list[CompanyRow]:
    return [m for m in members if _passes_cohort_quality_gate(subject, m, kind=kind)]


def construction_company_analytics_clause():
    """SQL filter for construction CI multi-company pools (excludes alias + probable_person)."""
    return company_analytics_entity_filter()


def filter_construction_peer_pool(members: list[CompanyRow]) -> list[CompanyRow]:
    """Post-filter construction peers: drop any row that looks like an
    individual, regardless of its entity_role -- see
    ``_is_person_like_construction_peer``."""
    return [m for m in members if not _is_person_like_construction_peer(m)]


def _is_person_like_construction_peer(member: CompanyRow) -> bool:
    """True when a construction-cohort row must never surface as a Market
    peer (Top competitors, Potential opportunity gaps, Competitor
    fit-scoring coverage).

    Two independent, rule-based signals, neither of which hardcodes a
    specific name:

    - entity_role in COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES (applicant_alias,
      probable_person). company_analytics_entity_filter() already applies
      this at SQL level for the initial fetch; repeated here so this
      function is a complete, self-contained guard usable on rows from any
      source (SQL fetch, cohort-isolation output, or a future path that
      doesn't apply the SQL clause).
    - is_probable_person_name(name), applied regardless of entity_role.
      This is what catches a row that carries any other entity_role
      (including one erroneously left/marked "canonical" by the merge
      pipeline) despite its name reading as an individual -- a hardening
      gap distinct from, and in addition to, the confirmed standalone
      production leak (see PR-MARKET-2A).
    """
    role = getattr(member, "entity_role", "") or ""
    if role in COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES:
        return True
    return is_probable_person_name(getattr(member, "name", "") or "")


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

    if kind == "construction" and model is Company:
        query = query.where(construction_company_analytics_clause())

    if use_city and city and kind == "construction":
        query = query.where(model.primary_city.ilike(city))

    rows = list(session.scalars(query.order_by(model.id.asc()).limit(limit)).all())
    return filter_construction_peer_pool(rows) if kind == "construction" else rows


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
        areas = [
            a.lower()
            for a in (getattr(member, "website_service_areas", None) or [])
            if a
        ]
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

    members = _fetch_cohort_rows(
        session, subject=subject, kind=kind, use_city=True, city=city
    )
    if kind == "architecture" and city:
        members = _filter_arch_city(members, subject, subject_cip, city)
    members = apply_cohort_type_isolation(
        members, subject, kind=kind, subject_cip=subject_cip, session=session
    )
    members = _apply_cohort_quality_gate(members, subject, kind=kind)
    if kind == "construction":
        # Defense-in-depth layer 2: cohort-type isolation and the quality
        # gate only ever narrow the set, never add rows, so this is
        # normally a no-op on top of the fetch-time filter in
        # _fetch_cohort_rows -- it exists so a future change to either
        # step can never silently reintroduce a person-like row here.
        members = filter_construction_peer_pool(members)

    definition_key = "sector_and_city"
    if city:
        definition = f"dominant_sector={sector}, city={city}"
    else:
        definition = f"dominant_sector={sector}"

    if len(members) < 8:
        members = _fetch_cohort_rows(
            session, subject=subject, kind=kind, use_city=False, city=city
        )
        members = apply_cohort_type_isolation(
            members, subject, kind=kind, subject_cip=subject_cip, session=session
        )
        members = _apply_cohort_quality_gate(members, subject, kind=kind)
        if kind == "construction":
            members = filter_construction_peer_pool(members)
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
