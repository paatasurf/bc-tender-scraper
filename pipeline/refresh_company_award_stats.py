from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Company, ContractAward
from pipeline.competitive_intel.awards import AwardCountResolver

MAX_LIST_ITEMS = 15

# Award intelligence columns only — permit stats are never overwritten.
AWARD_INTELLIGENCE_COLUMNS = (
    "award_count",
    "total_award_value",
    "avg_award_value",
    "award_categories",
    "award_clients",
    "buyer_levels",
    "award_sources",
    "first_award_date",
    "last_award_date",
    "primary_address",
    "primary_city",
    "primary_province",
    "data_sources",
)


@dataclass
class _AwardCompanyStats:
    award_count: int = 0
    total_award_value: float = 0.0
    categories: Counter[str] = field(default_factory=Counter)
    clients: Counter[str] = field(default_factory=Counter)
    buyer_levels: Counter[str] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    addresses: Counter[str] = field(default_factory=Counter)
    cities: Counter[str] = field(default_factory=Counter)
    provinces: Counter[str] = field(default_factory=Counter)
    first_award_date: str = ""
    last_award_date: str = ""


def _top_items(counter: Counter[str], limit: int = MAX_LIST_ITEMS) -> list[str]:
    return [item for item, _ in counter.most_common(limit) if item]


def _merge_data_sources(existing: list[str] | None, *, has_permits: bool) -> list[str]:
    merged = {source for source in (existing or []) if source}
    if has_permits:
        merged.add("permits")
    merged.add("contract_awards")
    return sorted(merged)


def _aggregate_award_stats(session: Session) -> dict[int, _AwardCompanyStats]:
    rows = session.scalars(
        select(ContractAward).where(
            ContractAward.company_id.isnot(None),
            ContractAward.winner_company != "",
        )
    ).all()

    stats_by_company: dict[int, _AwardCompanyStats] = {}
    for award in rows:
        company_id = award.company_id
        if company_id is None:
            continue

        entry = stats_by_company.setdefault(company_id, _AwardCompanyStats())
        entry.award_count += 1
        if award.award_value is not None:
            entry.total_award_value += float(award.award_value)

        category = (award.procurement_category or "").strip()
        if category:
            entry.categories[category] += 1

        client = (award.buyer_organization or "").strip()
        if client:
            entry.clients[client] += 1

        buyer_level = (award.buyer_level or "").strip()
        if buyer_level:
            entry.buyer_levels[buyer_level] += 1

        source = (award.source or "").strip()
        if source:
            entry.sources[source] += 1

        address = (award.winner_address or "").strip()
        if address:
            entry.addresses[address] += 1

        city = (award.winner_city or "").strip()
        if city:
            entry.cities[city] += 1

        province = (award.winner_province or "").strip()
        if province:
            entry.provinces[province] += 1

        award_date = (award.award_date or "").strip()
        if award_date:
            if not entry.first_award_date or award_date < entry.first_award_date:
                entry.first_award_date = award_date
            if not entry.last_award_date or award_date > entry.last_award_date:
                entry.last_award_date = award_date

    return stats_by_company


def _merge_award_stats(target: _AwardCompanyStats, source: _AwardCompanyStats) -> None:
    target.award_count += source.award_count
    target.total_award_value += source.total_award_value
    target.categories.update(source.categories)
    target.clients.update(source.clients)
    target.buyer_levels.update(source.buyer_levels)
    target.sources.update(source.sources)
    target.addresses.update(source.addresses)
    target.cities.update(source.cities)
    target.provinces.update(source.provinces)
    if source.first_award_date and (
        not target.first_award_date or source.first_award_date < target.first_award_date
    ):
        target.first_award_date = source.first_award_date
    if source.last_award_date and (
        not target.last_award_date or source.last_award_date > target.last_award_date
    ):
        target.last_award_date = source.last_award_date


def _pick_primary_address(addresses: Counter[str]) -> str:
    if not addresses:
        return ""
    return max(addresses.keys(), key=lambda value: (addresses[value], len(value)))


def _pick_primary_location(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def refresh_company_award_stats(session: Session) -> dict[str, Any]:
    """Populate award intelligence fields from linked contract_awards rows.

    Updates only AWARD_INTELLIGENCE_COLUMNS and preserves permit statistics.
    """
    print("[AwardCompanies] Aggregating linked contract awards by company...")
    stats_by_company = _aggregate_award_stats(session)
    if not stats_by_company:
        print("[AwardCompanies] No linked awards found — nothing to refresh")
        return {
            "companies_updated": 0,
            "overlap_companies": 0,
        }

    resolver = AwardCountResolver(session)
    touched_ids: set[int] = set()
    for company_id in stats_by_company:
        touched_ids |= resolver.sibling_ids(company_id)

    companies = session.scalars(select(Company).where(Company.id.in_(touched_ids))).all()

    updated = 0
    overlap = 0
    for company in companies:
        merged = _AwardCompanyStats()
        for sibling_id in resolver.sibling_ids(company.id, company):
            sibling_stats = stats_by_company.get(sibling_id)
            if sibling_stats is not None:
                _merge_award_stats(merged, sibling_stats)
        if merged.award_count == 0:
            continue

        has_permits = (company.total_projects or 0) > 0
        if has_permits:
            overlap += 1

        company.award_count = merged.award_count
        company.total_award_value = round(merged.total_award_value, 2)
        company.avg_award_value = (
            round(merged.total_award_value / merged.award_count, 2) if merged.award_count else 0.0
        )
        company.award_categories = _top_items(merged.categories)
        company.award_clients = _top_items(merged.clients)
        company.buyer_levels = _top_items(merged.buyer_levels)
        company.award_sources = _top_items(merged.sources)
        company.first_award_date = merged.first_award_date
        company.last_award_date = merged.last_award_date
        company.primary_address = _pick_primary_address(merged.addresses)[:500]
        company.primary_city = _pick_primary_location(merged.cities)[:100]
        company.primary_province = _pick_primary_location(merged.provinces)[:50]
        company.data_sources = _merge_data_sources(company.data_sources, has_permits=has_permits)
        updated += 1

    session.commit()
    print(f"[AwardCompanies] Refreshed award stats for {updated} companies ({overlap} permit overlap)")
    return {
        "companies_updated": updated,
        "overlap_companies": overlap,
    }
