"""Competitive Intelligence Score for contract awards."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.explain import ScoredRecommendation, build_reasons, weighted_fit


def score_contract_award(profile: CapabilityProfile, opp: NormalizedOpportunity) -> ScoredRecommendation:
    hay = opp.text_blob.lower()
    cat_raw = 85 if any(c.lower() in hay for c in profile.award_categories) else 35
    client_raw = 90 if any(c.lower() in hay for c in profile.award_clients) else 30
    cap_raw = 70 if opp.context == "own_history" else 55

    fit, breakdown = weighted_fit(
        [
            ("capability_fit", "Category relevance", cat_raw, 20, ""),
            ("project_type_fit", "Procurement fit", cap_raw, 25, opp.context),
            ("similar_projects", "Client relevance", client_raw, 20, ""),
            ("budget_fit", "Award value signal", 60, 15, ""),
            ("geography", "Delivery region fit", 55, 10, ""),
            ("contract_award_signal", "Award intelligence", 65, 10, ""),
        ]
    )
    score = min(92, fit)
    return ScoredRecommendation(
        score=score,
        score_label="Competitive Intelligence Score",
        rank_key=float(score),
        breakdown=breakdown,
        reasons=build_reasons(breakdown),
    )
