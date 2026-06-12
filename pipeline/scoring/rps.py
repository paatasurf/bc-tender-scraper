"""Revenue Pursuit Score for Active Opportunities (tenders only)."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.explain import BreakdownFactor, ScoredRecommendation, build_reasons, weighted_fit
from pipeline.scoring.revenue_rank import rank_key
from pipeline.taxonomy import capability_match_score


def _overlap_score(needles: list[str], haystack: str) -> tuple[int, str]:
    hay = haystack.lower()
    matched = [n for n in needles if n and n.lower() in hay]
    if not matched:
        return 0, ""
    return min(100, 40 + len(matched) * 15), ", ".join(matched[:3])


def _size_score(baseline: float, value: float) -> tuple[int, str]:
    if baseline <= 0 or value <= 0:
        return 50, "Value not stated"
    ratio = value / baseline
    if 0.5 <= ratio <= 2.0:
        return 95, "Project size aligned with typical work"
    if 0.25 <= ratio <= 4.0:
        return 75, "Project size within broader range"
    if 0.1 <= ratio <= 10.0:
        return 45, "Project size stretch"
    return 10, "Project size mismatch"


def _geo_score(profile: CapabilityProfile, opp: NormalizedOpportunity) -> tuple[int, str]:
    hay = f"{opp.geography_text} {opp.organization} {opp.title}".lower()
    for city in profile.service_cities:
        if city and city.lower() in hay:
            return 90, f"Same service area: {city}"
    for hood in profile.neighborhoods[:8]:
        if hood and hood.lower() in hay:
            return 80, f"Neighborhood overlap: {hood}"
    if "vancouver" in hay or "british columbia" in hay or " bc" in hay:
        return 65, "British Columbia market"
    return 30, "Outside core service area"


def _award_signal_raw(profile: CapabilityProfile, opp: NormalizedOpportunity) -> tuple[int, str]:
    hay = f"{opp.title} {opp.organization} {opp.text_blob}".lower()
    score = 0
    details: list[str] = []
    for cat in profile.award_categories[:8]:
        if cat and cat.lower() in hay:
            score += 40
            details.append(f"Category history: {cat}")
            break
    for client in profile.award_clients[:8]:
        if client and client.lower() in hay:
            score += 35
            details.append(f"Known client: {client}")
            break
    if profile.market_segments and opp.market_segment in profile.market_segments:
        score += 25
        details.append(f"Segment fit: {opp.market_segment}")
    return min(100, score), "; ".join(details)


def _weight_table(profile: CapabilityProfile) -> tuple[int, ...]:
    if profile.kind == "architecture":
        return (28, 22, 18, 12, 12, 0, 8)
    if profile.company_type == "Trade Contractor":
        return (30, 15, 15, 15, 10, 12, 3)
    return (25, 20, 18, 15, 10, 10, 2)


def score_active_tender(
    profile: CapabilityProfile,
    opp: NormalizedOpportunity,
    *,
    permit_signal: int = 0,
) -> ScoredRecommendation:
    w_cap, w_ptype, w_hist, w_size, w_geo, w_award, w_perm = _weight_table(profile)

    cap_raw = capability_match_score(profile.primary_trade, profile.trade_tags, opp.trade_tags)
    ptype_raw, ptype_detail = _overlap_score(profile.project_types, opp.text_blob)
    hist_count = sum(1 for pt in profile.project_types if pt and pt.lower() in opp.text_blob.lower())
    hist_raw = min(100, 35 + hist_count * 15 + min(profile.own_permit_count, 20))
    hist_detail = f"{profile.own_permit_count} historical projects on profile"
    size_raw, size_detail = _size_score(profile.avg_award_value or profile.avg_project_value, opp.estimated_value)
    geo_raw, geo_detail = _geo_score(profile, opp)
    award_raw, award_detail = _award_signal_raw(profile, opp)

    fit, breakdown = weighted_fit(
        [
            ("capability_fit", "Trade specialization match", cap_raw, w_cap, ""),
            ("project_type_fit", "Project type match", ptype_raw, w_ptype, ptype_detail),
            ("similar_projects", "Similar completed projects", hist_raw, w_hist, hist_detail),
            ("budget_fit", "Project size match", size_raw, w_size, size_detail),
            ("geography", "Geographic match", geo_raw, w_geo, geo_detail),
            ("contract_award_signal", "Contract award signal", award_raw, w_award, award_detail),
            ("permit_signal", "Market demand signal", min(100, permit_signal), w_perm, ""),
        ]
    )

    boost = 0
    if award_raw >= 40:
        boost = 4
    if award_raw >= 70:
        boost = 8
    score = min(100, fit + boost)
    if boost:
        breakdown.append(
            BreakdownFactor(
                factor="award_confidence_boost",
                label="Award confidence boost",
                points=boost,
                max_points=8,
                detail=award_detail,
            )
        )

    return ScoredRecommendation(
        score=score,
        score_label="Revenue Pursuit Score",
        rank_key=rank_key(score, opp.estimated_value),
        breakdown=breakdown,
        reasons=build_reasons(breakdown),
    )
