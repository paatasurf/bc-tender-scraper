"""Market Pipeline Score for permits and future demand signals."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.explain import ScoredRecommendation, build_reasons, weighted_fit
from pipeline.taxonomy import capability_match_score


def score_pipeline_permit(profile: CapabilityProfile, opp: NormalizedOpportunity) -> ScoredRecommendation:
    cap_raw = capability_match_score(profile.primary_trade, profile.trade_tags, opp.trade_tags)
    ptype_raw = 70 if any(pt.lower() in opp.text_blob.lower() for pt in profile.project_types) else 35
    hist_raw = 85 if opp.context == "own_permit" else 40

    fit, breakdown = weighted_fit(
        [
            ("capability_fit", "Trade alignment", cap_raw, 10, ""),
            ("project_type_fit", "Permit type fit", ptype_raw, 25, opp.payload.get("type", "")),
            ("similar_projects", "Company permit history", hist_raw, 15, ""),
            ("budget_fit", "Project value signal", 60, 15, ""),
            ("geography", "Geographic match", 70, 20, opp.geography_text[:60]),
            ("permit_signal", "Market heat", 55, 15, "Local permit activity"),
        ]
    )
    score = min(75, fit)
    return ScoredRecommendation(
        score=score,
        score_label="Market Pipeline Score",
        rank_key=float(score),
        breakdown=breakdown,
        reasons=build_reasons(breakdown),
    )
