"""Relationship and growth opportunity scorers (Phase 1 lightweight)."""

from __future__ import annotations

from pipeline.capability_profile import CapabilityProfile
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.explain import ScoredRecommendation, build_reasons, weighted_fit
from pipeline.taxonomy import adjacent_trades, capability_match_score


def score_relationship(
    profile: CapabilityProfile,
    *,
    entity_type: str,
    entity_name: str,
    project_count: int,
    related_tender_count: int = 0,
) -> ScoredRecommendation:
    rel_raw = min(100, 40 + project_count * 8)
    tender_raw = min(100, related_tender_count * 25)
    fit, breakdown = weighted_fit(
        [
            ("capability_fit", "Trade alignment", 70, 30, ""),
            ("similar_projects", "Shared project history", rel_raw, 40, f"{project_count} projects"),
            ("contract_award_signal", "Linked active opportunities", tender_raw, 30, ""),
        ]
    )
    score = min(100, fit)
    return ScoredRecommendation(
        score=score,
        score_label="Relationship Score",
        rank_key=float(score),
        breakdown=breakdown,
        reasons=[f"Repeat {entity_type}: {entity_name}", *build_reasons(breakdown)[:2]],
    )


def score_growth_tender(profile: CapabilityProfile, opp: NormalizedOpportunity) -> ScoredRecommendation:
    adjacent = set(adjacent_trades(profile.primary_trade))
    in_adjacent = any(tag in adjacent for tag in opp.trade_tags)
    cap_raw = 35 if not in_adjacent else capability_match_score(
        profile.primary_trade, profile.trade_tags, opp.trade_tags
    )
    fit, breakdown = weighted_fit(
        [
            ("capability_fit", "Adjacent trade fit", cap_raw, 35, ""),
            ("project_type_fit", "Project type overlap", 55, 25, ""),
            ("similar_projects", "Partial experience", 50, 20, ""),
            ("budget_fit", "Size fit", 50, 20, ""),
        ]
    )
    score = min(85, fit)
    trade = opp.trade_tags[0] if opp.trade_tags else "related work"
    return ScoredRecommendation(
        score=score,
        score_label="Growth Opportunity Score",
        rank_key=float(score),
        breakdown=breakdown,
        reasons=[f"Adjacent expansion: {trade}", *build_reasons(breakdown)[:2]],
    )
