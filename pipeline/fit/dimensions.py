"""Six-dimensional business fit scoring (Iteration B tuned)."""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.business_attributes import (
    infer_delivery,
    infer_sector,
    parse_city_from_address,
)
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.fit.scope_policy import is_architecture_design_path
from pipeline.fit.geo_policy import (
    infer_opportunity_trades,
    is_remote_federal_for_local_company,
    is_trade_specialist,
    strong_trade_match,
)
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.taxonomy import capability_match_score

# Stable semantic identifier for this six-dimension fit-scoring contract.
# Bump only when a scoring formula/threshold in this module actually
# changes -- see PR-E1.
FIT_DIMENSIONS_ALGORITHM_VERSION = "fit_dimensions_v1"


@dataclass
class FitDimension:
    name: str
    score: int
    passed: bool
    reason: str

    @property
    def algorithm_version(self) -> str:
        return FIT_DIMENSIONS_ALGORITHM_VERSION

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "passed": self.passed,
            "reason": self.reason,
            "algorithm_version": self.algorithm_version,
        }


def _company_trades(cip: CompanyIntelligenceProfile) -> list[str]:
    return [cip.primary_trade, *cip.secondary_trades]


def score_business_fit(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> FitDimension:
    trades = _company_trades(cip)
    opp_trades = infer_opportunity_trades(opp)
    if "unclassified" in opp_trades and len(opp_trades) == 1:
        raw = 25
        reason = "Opportunity trade scope unclear"
    else:
        raw = capability_match_score(cip.primary_trade, trades, opp_trades)

    if cip.work_orientation == "construction" and opp.orientation == "maintenance":
        raw = min(raw, 20)
        reason = "Maintenance procurement vs construction-oriented company"
    elif is_architecture_design_path(cip, opp):
        raw = max(raw, 82)
        reason = "Genuine design RFP matches architecture firm profile"
    elif cip.entity_class == "designer" and opp.delivery_type not in (
        "design",
        "renovation",
        "new_build",
        "",
    ):
        raw = min(raw, 35)
        reason = "Design firm — limited fit for non-design scope"
    elif cip.entity_class == "designer" and opp.delivery_type == "design":
        raw = max(raw, 85)
        reason = "Design services match architecture profile"
    else:
        reason = f"Trade alignment: {cip.primary_trade} vs {', '.join(opp_trades[:3]) or 'unknown'}"

    threshold = 60 if opp.category == "active" else 45
    if is_architecture_design_path(cip, opp):
        threshold = 45
    elif is_trade_specialist(cip) and raw >= 75:
        threshold = 50

    return FitDimension("business_fit", raw, raw >= threshold, reason)


def score_project_type_fit(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> FitDimension:
    delivery = opp.delivery_type or infer_delivery(opp.text_blob)
    company_deliveries = set(cip.delivery_types)
    cluster_deliveries = {cl.delivery for cl in cip.project_clusters}

    if is_architecture_design_path(cip, opp):
        spec_hit = any(
            s.lower() in opp.text_blob.lower() for s in cip.specializations if s
        )
        raw = 90 if spec_hit else 80
        reason = "Design delivery matches architecture practice"
    elif delivery in company_deliveries or delivery in cluster_deliveries:
        raw = 85
        reason = f"Delivery type '{delivery}' matches company history"
    elif delivery in cip.normalized_project_types:
        raw = 75
        reason = "Normalized project type overlap"
    else:
        overlap = sum(
            1
            for pt in cip.normalized_project_types
            if pt and pt.lower() in opp.text_blob.lower()
        )
        raw = min(70, 25 + overlap * 20) if overlap else 15
        reason = "No delivery type overlap with company project history"

    is_strong, trade_score = strong_trade_match(cip, opp)
    if is_trade_specialist(cip) and is_strong:
        raw = max(raw, 55)
        reason = (
            f"Strong trade match ({trade_score}) — scope aligns with specialist profile"
        )

    threshold = 40 if opp.category == "active" else 35
    if is_architecture_design_path(cip, opp):
        threshold = 35
    elif is_trade_specialist(cip) and is_strong:
        threshold = 35
    return FitDimension("project_type_fit", raw, raw >= threshold, reason)


def score_sector_fit(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> FitDimension:
    sector = opp.sector or infer_sector(opp.text_blob)
    share = cip.sector_focus.get(sector, 0.0)

    if is_architecture_design_path(cip, opp):
        raw = 75 if sector in {"institutional", "commercial", "public"} else 55
        reason = f"Design opportunity in {sector} — typical architecture sector"
        return FitDimension("sector_fit", raw, raw >= 40, reason)

    if share >= 0.25:
        raw = min(95, int(60 + share * 40))
        reason = f"Sector '{sector}' is {share:.0%} of company focus"
    elif sector == cip.dominant_sector:
        raw = 70
        reason = f"Dominant sector match: {sector}"
    elif sector in cip.growth_direction or any(
        sector in g for g in cip.growth_direction
    ):
        raw = 55
        reason = f"Adjacent sector: {sector}"
    else:
        raw = 20
        reason = f"Sector '{sector}' outside company focus ({cip.dominant_sector})"

    is_strong, _ = strong_trade_match(cip, opp)
    if is_trade_specialist(cip) and sector == "commercial":
        raw = max(raw, 55)
        reason = "Commercial sector — typical for trade contractors"
    elif is_trade_specialist(cip) and is_strong:
        raw = max(raw, 50)
        reason = f"Trade specialist — {sector} opportunity within specialist scope"

    threshold = 50 if opp.category == "active" else 40
    if is_trade_specialist(cip):
        threshold = 45
    return FitDimension("sector_fit", raw, raw >= threshold, reason)


def score_geography_fit(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> FitDimension:
    hay = f"{opp.title} {opp.geography_text} {opp.organization}".lower()
    best = 0
    best_reason = "Outside core service geography"

    for geo in cip.concentration_map:
        if geo.geo.lower() in hay:
            raw = min(95, int(55 + geo.share * 40))
            if raw > best:
                best = raw
                best_reason = f"{geo.geo} — {geo.share:.0%} of project concentration"

    for city in cip.service_cities:
        if city and city.lower() in hay:
            best = max(best, 80)
            best_reason = f"Service city match: {city}"

    for hood in cip.neighborhoods[:6]:
        if hood and hood.lower() in hay:
            best = max(best, 75)
            best_reason = f"Neighborhood match: {hood}"

    is_strong_trade, trade_score = strong_trade_match(cip, opp)

    if is_remote_federal_for_local_company(cip, opp):
        best = min(best, 30)
        best_reason = "Federal opportunity outside company service geography"
    elif is_trade_specialist(cip) and is_strong_trade and opp.buyer_type == "federal":
        if "british columbia" in hay or " bc" in hay:
            best = max(best, 62)
            best_reason = (
                f"Trade match ({trade_score}) — BC federal work in specialist scope"
            )
    elif is_trade_specialist(cip) and is_strong_trade and best == 0:
        if "british columbia" in hay or " bc" in hay or "vancouver" in hay:
            best = 58
            best_reason = (
                f"Strong trade match ({trade_score}) — regional BC opportunity"
            )
    elif best == 0 and cip.geographic_reach in {"provincial", "national"}:
        if "british columbia" in hay or " bc" in hay:
            best = 55
            best_reason = "Provincial reach — BC-wide opportunity"
    elif best == 0 and cip.geographic_reach == "regional":
        if "british columbia" in hay or " bc" in hay:
            best = 45
            best_reason = "Regional company — weak BC match"

    if best == 0:
        parsed = parse_city_from_address(opp.geography_text)
        if parsed and any(
            parsed.lower() == g.geo.lower() for g in cip.concentration_map
        ):
            best = 70
            best_reason = f"Parsed location {parsed} in concentration map"

    threshold = 55 if opp.category == "active" else 50
    if is_architecture_design_path(cip, opp):
        threshold = 45
    elif is_trade_specialist(cip) and is_strong_trade:
        threshold = 45
    elif (
        cip.entity_class == "consultant"
        and opp.buyer_type == "federal"
        and cip.public_private_ratio >= 0.3
    ):
        if not is_remote_federal_for_local_company(cip, opp):
            threshold = 45
            if best < 45 and ("bc" in hay or "british columbia" in hay):
                best = max(best, 50)
                best_reason = (
                    "Consulting firm with public-sector history — BC federal/provincial"
                )

    return FitDimension("geography_fit", best, best >= threshold, best_reason)


def score_value_fit(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> FitDimension:
    value = opp.estimated_value
    vr = cip.value_range
    if value <= 0 or vr.median <= 0:
        raw = 50
        reason = "Value not stated — neutral"
        passed = True
    else:
        low = vr.p25 * 0.5 if vr.p25 > 0 else vr.median * 0.25
        high = vr.p75 * 3 if vr.p75 > 0 else vr.median * 4
        if low <= value <= high:
            raw = 85
            reason = f"${value:,.0f} within typical range (${low:,.0f}–${high:,.0f})"
            passed = True
        elif value < low:
            raw = 25
            reason = f"${value:,.0f} below typical minimum (~${low:,.0f})"
            passed = opp.category != "active"
        else:
            raw = 30
            reason = f"${value:,.0f} above typical maximum (~${high:,.0f})"
            passed = cip.deal_size_band in {"large", "mega"} and value <= vr.max * 5

    threshold = 45
    return FitDimension(
        "value_fit", raw, passed and raw >= threshold if value > 0 else passed, reason
    )


def score_client_fit(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> FitDimension:
    org = (opp.organization or "").lower()
    buyer = opp.buyer_type or ""

    for client in cip.repeat_clients:
        if client and client.lower() in org:
            return FitDimension("client_fit", 95, True, f"Repeat client: {client}")

    for client in cip.award_clients:
        if client and client.lower() in org:
            return FitDimension("client_fit", 85, True, f"Known client: {client}")

    if buyer and buyer in cip.buyer_types:
        return FitDimension("client_fit", 70, True, f"Known buyer type: {buyer}")

    if opp.category == "intelligence":
        raw = 40 if opp.context == "own_history" else 25
        return FitDimension(
            "client_fit", raw, raw >= 40, "Award intelligence — limited client linkage"
        )

    raw = 35 if buyer else 30
    return FitDimension(
        "client_fit",
        raw,
        raw >= 35 or opp.category == "pipeline",
        "No direct client history",
    )


def compute_all_fits(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> dict[str, FitDimension]:
    return {
        "business_fit": score_business_fit(cip, opp),
        "project_type_fit": score_project_type_fit(cip, opp),
        "sector_fit": score_sector_fit(cip, opp),
        "geography_fit": score_geography_fit(cip, opp),
        "value_fit": score_value_fit(cip, opp),
        "client_fit": score_client_fit(cip, opp),
    }
