"""Competitive Threat Score — deterministic weighted sum."""

from __future__ import annotations

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.competitive_intel.activity import award_activity_raw, permit_activity_raw
from pipeline.competitive_intel.overlap import (
    category_overlap_raw,
    geographic_overlap_raw,
    value_overlap_raw,
)
from pipeline.competitive_intel.types import ActivityStats, CompanyRow, Kind, ThreatScoreResult
from pipeline.scoring.explain import build_reasons, weighted_fit
from pipeline.scoring.match_scoring_common import assert_score_equals_breakdown


def confidence_label(raw_components: dict[str, float], *, kind: Kind) -> str:
    active = sum(1 for k, v in raw_components.items() if v > 0 and k != "award_activity")
    if kind == "architecture":
        active = sum(1 for k, v in raw_components.items() if v > 0 and k != "award_activity")
    else:
        active = sum(1 for v in raw_components.values() if v > 0)
    if active >= 4:
        return "high"
    if active >= 2:
        return "medium"
    return "low"


def compute_threat_score(
    *,
    subject: CompanyRow,
    peer: CompanyRow,
    subject_cip: CompanyIntelligenceProfile,
    peer_cip: CompanyIntelligenceProfile,
    kind: Kind,
    stats: ActivityStats,
    include_permit_scan: bool = False,
) -> ThreatScoreResult:
    geo_raw, geo_detail = geographic_overlap_raw(subject_cip, peer_cip, subject, peer)
    cat_raw, cat_detail = category_overlap_raw(subject_cip, peer_cip, subject, peer)
    val_raw, val_detail = value_overlap_raw(subject_cip, peer_cip, subject, peer)

    subject_clients = list(getattr(subject, "award_clients", None) or []) or subject_cip.award_clients
    peer_clients = list(getattr(peer, "award_clients", None) or []) or peer_cip.award_clients
    awd_raw, awd_detail = award_activity_raw(
        peer_id=peer.id,
        subject_clients=subject_clients,
        peer_clients=peer_clients,
        stats=stats,
        kind=kind,
    )
    perm_raw, perm_detail = permit_activity_raw(
        peer=peer,
        peer_id=peer.id,
        stats=stats,
        include_permit_scan=include_permit_scan,
    )

    raw_components = {
        "geographic_overlap": geo_raw,
        "category_overlap": cat_raw,
        "value_overlap": val_raw,
        "award_activity": awd_raw,
        "permit_activity": perm_raw,
    }

    factors = [
        ("geographic_overlap", "Geographic overlap", int(round(geo_raw)), 25, geo_detail),
        ("category_overlap", "Category overlap", int(round(cat_raw)), 25, cat_detail),
        ("value_overlap", "Value overlap", int(round(val_raw)), 20, val_detail),
        ("award_activity", "Award activity", int(round(awd_raw)), 15, awd_detail),
        ("permit_activity", "Permit activity", int(round(perm_raw)), 15, perm_detail),
    ]
    score, breakdown = weighted_fit(factors)
    reasons = build_reasons(breakdown, limit=3)
    confidence = confidence_label(raw_components, kind=kind)

    api_breakdown = {b.factor: {"points": b.points, "detail": b.detail} for b in breakdown}
    assert_score_equals_breakdown(score, api_breakdown)

    return ThreatScoreResult(
        score=score,
        breakdown=breakdown,
        reasons=reasons,
        confidence=confidence,
        raw_components=raw_components,
    )
