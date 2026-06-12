"""Effective profile confidence with permit-history bootstrap (Iteration C)."""

from __future__ import annotations

from pipeline.cip_schema import CompanyIntelligenceProfile, GeoConcentration, ProjectCluster
from pipeline.fit.geo_policy import TRADE_SPECIALIST_TRADES


def bootstrap_from_signals(
    *,
    own_permit_count: int,
    project_clusters: list[ProjectCluster],
    concentration_map: list[GeoConcentration],
    normalized_project_types: list[str],
    trade_sources: list[str],
    primary_trade: str,
    sector_focus: dict[str, float],
) -> float:
    """Raise confidence for companies with substantial permit history and patterns."""
    score = 0.35

    if own_permit_count >= 5:
        score = max(score, 0.42)
    if own_permit_count >= 20:
        score = max(score, 0.48)
    if own_permit_count >= 50:
        score = max(score, 0.55)
    if own_permit_count >= 100:
        score = max(score, 0.62)

    if project_clusters:
        top_share = max(c.share for c in project_clusters)
        top_count = max(c.count for c in project_clusters)
        if top_share >= 0.5 and top_count >= 3:
            score = max(score, 0.58)
        if top_count >= 5:
            score = max(score, 0.55)

    if concentration_map:
        top_geo = max(g.share for g in concentration_map)
        if top_geo >= 0.4 and concentration_map[0].project_count >= 3:
            score = max(score, 0.52)

    if len(normalized_project_types) >= 2:
        score = max(score, 0.48)
    if len(set(normalized_project_types)) >= 3:
        score = max(score, 0.52)

    if "name" in trade_sources:
        score = max(score, 0.5)
    if "permits" in trade_sources:
        score = max(score, 0.48)

    if primary_trade in TRADE_SPECIALIST_TRADES and own_permit_count >= 20:
        score = max(score, 0.55)
    if primary_trade in TRADE_SPECIALIST_TRADES and own_permit_count >= 50:
        score = max(score, 0.62)

    if sector_focus:
        top_sector = max(sector_focus.values())
        if top_sector >= 0.6:
            score = max(score, 0.5 + top_sector * 0.2)

    return round(min(0.95, score), 2)


def bootstrap_profile_confidence(cip: CompanyIntelligenceProfile) -> float:
    return bootstrap_from_signals(
        own_permit_count=cip.own_permit_count,
        project_clusters=cip.project_clusters,
        concentration_map=cip.concentration_map,
        normalized_project_types=cip.normalized_project_types,
        trade_sources=cip.trade_sources,
        primary_trade=cip.primary_trade,
        sector_focus=cip.sector_focus,
    )


def effective_profile_confidence(cip: CompanyIntelligenceProfile) -> float:
    boot = bootstrap_profile_confidence(cip)
    return round(max(cip.specialization_confidence, boot), 2)


def meets_active_profile_threshold(cip: CompanyIntelligenceProfile, *, minimum: float = 0.45) -> bool:
    return effective_profile_confidence(cip) >= minimum
