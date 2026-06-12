"""Geography and profile-context policies for fit tuning."""

from __future__ import annotations

import re

from pipeline.business_attributes import BC_CITIES, parse_city_from_address
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.taxonomy import TRADE_PATTERNS, capability_match_score

TRADE_SPECIALIST_TRADES = frozenset(
    {
        "electrical",
        "mechanical",
        "hvac",
        "plumbing",
        "concrete",
        "roofing",
        "demolition",
        "glazing",
        "landscaping",
        "civil",
        "structural",
    }
)

REMOTE_FEDERAL_LOCATIONS = frozenset(
    {
        "penticton",
        "masset",
        "prince george",
        "terrace",
        "dawson creek",
        "fort st. john",
        "kamloops",
        "kelowna",
        "vernon",
        "alaska highway",
        "yukon",
        "prince rupert",
        "cranbrook",
        "nelson",
        "interior",
        "sandspit",
    }
)


def is_trade_specialist(cip: CompanyIntelligenceProfile) -> bool:
    return (
        cip.company_type == "Trade Contractor"
        or cip.primary_trade in TRADE_SPECIALIST_TRADES
        or any(t in TRADE_SPECIALIST_TRADES for t in cip.secondary_trades)
    )


def infer_opportunity_trades(opp: NormalizedOpportunity) -> list[str]:
    if opp.trade_tags and opp.trade_tags != ["unclassified"]:
        return list(opp.trade_tags)
    blob = f"{opp.title} {opp.text_blob} {opp.organization}"
    hits: list[str] = []
    for trade, pattern in TRADE_PATTERNS:
        if pattern.search(blob):
            hits.append(trade)
    return hits or ["unclassified"]


def strong_trade_match(cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity) -> tuple[bool, int]:
    opp_trades = infer_opportunity_trades(opp)
    score = capability_match_score(
        cip.primary_trade,
        [cip.primary_trade, *cip.secondary_trades],
        opp_trades,
    )
    return score >= 80, score


def _company_geos(cip: CompanyIntelligenceProfile) -> set[str]:
    geos = {g.geo.lower() for g in cip.concentration_map if g.geo and g.geo.lower() != "unknown"}
    for city in cip.service_cities:
        if city and len(city) < 40 and not any(c.isdigit() for c in city[:3]):
            geos.add(city.lower())
    for hood in cip.neighborhoods:
        if hood:
            geos.add(hood.lower())
    for cl in cip.project_clusters:
        if cl.geo and cl.geo.lower() != "unknown":
            geos.add(cl.geo.lower())
    return geos


def _geo_overlap(loc: str, company_geos: set[str]) -> bool:
    for cg in company_geos:
        if loc in cg or cg in loc:
            return True
    return False


def opportunity_locations(opp: NormalizedOpportunity) -> set[str]:
    hay = f"{opp.title} {opp.geography_text} {opp.organization}".lower()
    found: set[str] = set()
    for city in BC_CITIES:
        if city in hay:
            found.add(city)
    for remote in REMOTE_FEDERAL_LOCATIONS:
        if remote in hay:
            found.add(remote)
    parsed = parse_city_from_address(opp.geography_text)
    if parsed:
        found.add(parsed.lower())
    return found


def is_remote_federal_location(opp: NormalizedOpportunity) -> bool:
    locs = opportunity_locations(opp)
    return bool(locs & REMOTE_FEDERAL_LOCATIONS)


def is_remote_federal_for_local_company(cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity) -> bool:
    if opp.buyer_type != "federal" and opp.subtype != "federal_tender":
        return False

    locs = opportunity_locations(opp)
    if not locs:
        return False

    company_geos = _company_geos(cip)
    if not company_geos:
        return cip.geographic_reach == "local"

    remote_hits = locs & REMOTE_FEDERAL_LOCATIONS
    if remote_hits:
        if any(_geo_overlap(loc, company_geos) for loc in remote_hits):
            return False
        return True

    if cip.geographic_reach in {"provincial", "national"}:
        return False

    for loc in locs:
        if loc in BC_CITIES and not _geo_overlap(loc, company_geos):
            if cip.geographic_reach == "local":
                return True
    return False


def is_weak_consultant_federal(cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity) -> bool:
    """Provincial consultants must still align on remote federal work."""
    if cip.entity_class != "consultant":
        return False
    if opp.buyer_type != "federal" and opp.subtype != "federal_tender":
        return False
    if not is_remote_federal_location(opp):
        return False

    company_geos = _company_geos(cip)
    locs = opportunity_locations(opp)
    remote_hits = locs & REMOTE_FEDERAL_LOCATIONS
    if remote_hits and not any(_geo_overlap(loc, company_geos) for loc in remote_hits):
        return True

    sector = opp.sector or ""
    if sector and sector != cip.dominant_sector:
        share = cip.sector_focus.get(sector, 0.0)
        if share < 0.15:
            return True
    return False
