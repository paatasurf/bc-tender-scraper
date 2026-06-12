"""Company-first hard filters before scoring."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.taxonomy import capability_match_score


def passes_capability_gate(profile: CapabilityProfile, opp: NormalizedOpportunity, *, min_match: int = 15) -> bool:
    if opp.category == "pipeline" and opp.subtype == "building_permit":
        return True
    score = capability_match_score(profile.primary_trade, profile.trade_tags, opp.trade_tags)
    return score >= min_match


def passes_value_gate(profile: CapabilityProfile, opp: NormalizedOpportunity) -> bool:
    value = opp.estimated_value
    baseline = profile.avg_award_value or profile.avg_project_value
    if value <= 0 or baseline <= 0:
        return True
    ratio = value / baseline
    return 0.05 <= ratio <= 15.0


def passes_geography_gate(profile: CapabilityProfile, opp: NormalizedOpportunity) -> bool:
    if not profile.service_cities and not profile.neighborhoods:
        return True
    hay = f"{opp.title} {opp.geography_text} {opp.organization}".lower()
    if "british columbia" in hay or " bc" in hay or "vancouver" in hay:
        return True
    for city in profile.service_cities:
        if city and city.lower() in hay:
            return True
    for hood in profile.neighborhoods[:6]:
        if hood and hood.lower() in hay:
            return True
    if profile.kind == "architecture":
        return True
    return profile.primary_trade in {"general_building", "consulting", "engineering"}
