"""Award counts resolved from contract_awards with DBA vendor rollup."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from db.models import Company, ContractAward
from pipeline.company_classification import parse_name
from pipeline.company_matching import normalize_vendor_name
from pipeline.competitive_intel.cohort import (
    construction_company_analytics_clause,
    filter_construction_peer_pool,
)
from pipeline.competitive_intel.cohort_isolation import apply_cohort_type_isolation
from pipeline.competitive_intel.types import CompanyRow, Kind

MIN_VENDOR_KEY_LEN = 4
AWARD_MARKET_MIN_AWARDED = 3
AWARD_MARKET_QUERY_LIMIT = 200


def vendor_keys_from_name(name: str, canonical: str = "") -> set[str]:
    keys: set[str] = set()
    parsed = parse_name(name or "")
    for candidate in (name, canonical, parsed.get("dba") or "", parsed.get("legal") or ""):
        key = normalize_vendor_name(str(candidate))
        if len(key) >= MIN_VENDOR_KEY_LEN:
            keys.add(key)
    return keys


def _load_raw_award_counts(session: Session) -> dict[int, int]:
    rows = session.execute(
        select(ContractAward.company_id, func.count())
        .where(
            ContractAward.company_id.isnot(None),
            ContractAward.winner_company != "",
        )
        .group_by(ContractAward.company_id)
    ).all()
    return {int(company_id): int(count) for company_id, count in rows if company_id is not None}


def _load_vendor_groups(session: Session) -> tuple[dict[str, set[int]], dict[int, set[str]]]:
    key_to_ids: dict[str, set[int]] = defaultdict(set)
    id_to_keys: dict[int, set[str]] = {}
    for company_id, name, canonical in session.execute(
        select(Company.id, Company.name, Company.canonical_vendor_name).where(
            construction_company_analytics_clause()
        )
    ).all():
        keys = vendor_keys_from_name(name or "", canonical or "")
        id_to_keys[int(company_id)] = keys
        for key in keys:
            key_to_ids[key].add(int(company_id))
    return key_to_ids, id_to_keys


class AwardCountResolver:
    """Resolve award counts across DBA rows that share a normalized vendor identity."""

    def __init__(self, session: Session):
        self._raw = _load_raw_award_counts(session)
        self._key_to_ids, self._id_to_keys = _load_vendor_groups(session)

    def sibling_ids(self, company_id: int, company: CompanyRow | None = None) -> set[int]:
        keys = set(self._id_to_keys.get(company_id, set()))
        if company is not None:
            keys |= vendor_keys_from_name(
                getattr(company, "name", "") or "",
                getattr(company, "canonical_vendor_name", "") or "",
            )
        siblings = {company_id}
        for key in keys:
            siblings |= self._key_to_ids.get(key, set())
        return siblings

    def count_for(self, company: CompanyRow) -> int:
        return self.count_for_id(int(company.id), company)

    def count_for_id(self, company_id: int, company: CompanyRow | None = None) -> int:
        return sum(self._raw.get(sid, 0) for sid in self.sibling_ids(company_id, company))

    def counts_for_companies(self, companies: list[CompanyRow]) -> dict[int, int]:
        return {int(company.id): self.count_for(company) for company in companies}


def _sector_clause(model, subject: CompanyRow):
    sector = (getattr(subject, "dominant_sector", "") or "").strip()
    trade = (getattr(subject, "primary_trade", "") or "").strip()
    if sector and trade:
        return or_(model.dominant_sector == sector, model.primary_trade == trade)
    if sector:
        return model.dominant_sector == sector
    if trade:
        return model.primary_trade == trade
    return True


def select_award_market_members(
    session: Session,
    subject: CompanyRow,
    cohort_members: list[CompanyRow],
    resolver: AwardCountResolver,
    *,
    kind: Kind,
    subject_cip=None,
) -> list[CompanyRow]:
    """Peers used for Awards market median — permit cohort when rich, else sector award holders."""
    if kind != "construction":
        return cohort_members

    awarded_in_cohort = [member for member in cohort_members if resolver.count_for(member) > 0]
    if len(awarded_in_cohort) >= AWARD_MARKET_MIN_AWARDED:
        return awarded_in_cohort

    city = (getattr(subject, "primary_city", "") or "").strip()
    sector_filter = _sector_clause(Company, subject)

    def _awarded_candidates(*, use_city: bool) -> list[CompanyRow]:
        query = select(Company).where(
            Company.id != subject.id,
            construction_company_analytics_clause(),
        )
        if sector_filter is not True:
            query = query.where(sector_filter)
        if use_city and city:
            query = query.where(Company.primary_city.ilike(city))
        rows = list(session.scalars(query.limit(AWARD_MARKET_QUERY_LIMIT)).all())
        rows = filter_construction_peer_pool(rows)
        rows = apply_cohort_type_isolation(
            rows, subject, kind=kind, subject_cip=subject_cip, session=session
        )
        return [row for row in rows if resolver.count_for(row) > 0]

    awarded = _awarded_candidates(use_city=True)
    if len(awarded) < AWARD_MARKET_MIN_AWARDED:
        awarded = _awarded_candidates(use_city=False)

    if len(awarded) >= AWARD_MARKET_MIN_AWARDED:
        return awarded

    return cohort_members


def top_award_rival_counts(
    award_market_members: list[CompanyRow],
    award_counts: dict[int, int],
    *,
    limit: int = 5,
) -> list[int]:
    """Top award counts in the award market set — fallback when threat peers lack awards."""
    values = sorted(
        (int(award_counts.get(int(member.id), 0)) for member in award_market_members),
        reverse=True,
    )
    positive = [value for value in values if value > 0]
    return positive[:limit]
