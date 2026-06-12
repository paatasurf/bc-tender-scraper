"""Business Development Intelligence — business-fit-first recommendation engine."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from pipeline.cip_builder import cip_to_capability_profile, get_cip
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.fit.gates import (
    ACTIVE_BPS_THRESHOLD,
    GROWTH_BPS_THRESHOLD,
    INTEL_BPS_THRESHOLD,
    PIPELINE_BPS_THRESHOLD,
    evaluate_gates,
    intel_actionability_gate,
)
from pipeline.market_normalizer import (
    NormalizedOpportunity,
    load_active_tenders,
    load_intelligence_awards,
    load_pipeline_permits,
)
from pipeline.scoring.bps import compute_bps
from pipeline.scoring.relationship_growth import score_relationship

Kind = Literal["construction", "architecture"]

SECTION_CAPS = {
    "active": 5,
    "pipeline": 5,
    "intelligence": 5,
    "relationship": 3,
    "growth": 2,
}
GLOBAL_CAP = 20


def _item_payload(
    opp: NormalizedOpportunity,
    bps: Any,
    *,
    item_type: str,
    fit_assessment: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    explanation = bps.to_explanation_dict()
    base = {
        "item_type": item_type,
        "subtype": opp.subtype,
        "source_table": opp.source_table,
        "id": opp.source_id,
        "score": bps.score,
        "score_label": "Business Pursuit Score",
        "rank_key": bps.rank_key,
        "estimated_value": opp.estimated_value,
        "explanation": explanation,
        "fit_assessment": fit_assessment,
        "pursuit_verdict": bps.pursuit_verdict,
        "reasons": bps.reasons,
        "source": "rules",
        "context": opp.context,
        "payload": {
            **opp.payload,
            "buyer": opp.organization,
            "sector": opp.sector,
            "delivery_type": opp.delivery_type,
        },
        "evidence": {
            "buyer": opp.organization,
            "expected_value": opp.estimated_value,
            "sector": opp.sector,
            "geography": opp.geography_text[:120],
        },
    }
    if extra:
        base.update(extra)
    return base


def _related_tender_ids(active: list[dict[str, Any]], hay: str, limit: int = 3) -> list[int]:
    hay_lower = hay.lower()
    ids: list[int] = []
    for item in active:
        title = str(item.get("payload", {}).get("title", "")).lower()
        org = str(item.get("payload", {}).get("company", "")).lower()
        if any(token in title or token in org for token in hay_lower.split() if len(token) > 4):
            ids.append(int(item["id"]))
        if len(ids) >= limit:
            break
    return ids


def _process_pool(
    cip: CompanyIntelligenceProfile,
    pool: list[NormalizedOpportunity],
    section: Literal["active", "pipeline", "intelligence", "growth"],
    *,
    bps_threshold: int,
    active_items: list[dict[str, Any]] | None = None,
    include_rejections: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    shown: list[tuple[NormalizedOpportunity, Any, dict]] = []
    rejections: list[dict[str, Any]] = []
    stats = {"scanned": len(pool), "gate_rejected": 0, "bps_rejected": 0, "scored": 0, "shown": 0}

    for opp in pool:
        gate = evaluate_gates(cip, opp, section)
        if not gate.passed:
            stats["gate_rejected"] += 1
            if include_rejections and len(rejections) < 10:
                rejections.append(
                    {
                        "section": section,
                        "title": opp.title[:120],
                        "source": opp.subtype,
                        "rejection_code": gate.rejection_code,
                        "rejection_detail": gate.rejection_detail,
                        "fit_assessment": {k: v.to_dict() for k, v in gate.fits.items()},
                    }
                )
            continue

        if section == "intelligence" and active_items is not None:
            actionable, reason = intel_actionability_gate(cip, opp, active_items=active_items)
            if not actionable:
                stats["gate_rejected"] += 1
                if include_rejections and len(rejections) < 10:
                    rejections.append(
                        {
                            "section": section,
                            "title": opp.title[:120],
                            "rejection_code": "INTEL_NOT_ACTIONABLE",
                            "rejection_detail": reason,
                        }
                    )
                continue

        bps = compute_bps(cip, opp, gate.fits, section=section)
        stats["scored"] += 1
        if bps.score < bps_threshold:
            stats["bps_rejected"] += 1
            if include_rejections and len(rejections) < 10:
                rejections.append(
                    {
                        "section": section,
                        "title": opp.title[:120],
                        "score": bps.score,
                        "rejection_code": "BPS_BELOW_THRESHOLD",
                        "rejection_detail": f"Score {bps.score} < {bps_threshold}",
                        "fit_assessment": {k: v.to_dict() for k, v in gate.fits.items()},
                    }
                )
            continue

        fit_dict = {k: v.to_dict() for k, v in gate.fits.items()}
        shown.append((opp, bps, fit_dict))

    shown.sort(key=lambda row: row[1].rank_key, reverse=True)
    cap = SECTION_CAPS[section]
    items: list[dict[str, Any]] = []
    item_type_map = {
        "active": "tender",
        "pipeline": "permit",
        "intelligence": "contract_award",
        "growth": "tender",
    }
    for opp, bps, fit_dict in shown[:cap]:
        extra = {"growth": True} if section == "growth" else None
        if section == "intelligence" and active_items is not None:
            extra = {
                "related_active_tender_ids": _related_tender_ids(
                    active_items, f"{opp.organization} {opp.title}"
                ),
            }
        items.append(_item_payload(opp, bps, item_type=item_type_map[section], fit_assessment=fit_dict, extra=extra))

    stats["shown"] = len(items)
    return items, stats, rejections


def recommend_bd_intelligence(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    active_limit: int = 5,
    pipeline_limit: int = 5,
    intel_limit: int = 5,
    relationship_limit: int = 3,
    growth_limit: int = 2,
    refresh_profile: bool = False,
    max_candidates: int = 400,
    include_rejections: bool = False,
    min_bps: int | None = None,
) -> dict[str, Any]:
    cip = get_cip(session, company_id=company_id, kind=kind, refresh=refresh_profile)
    profile = cip_to_capability_profile(cip)

    active_threshold = min_bps if min_bps is not None else ACTIVE_BPS_THRESHOLD

    tender_pool = load_active_tenders(session, kind, limit=max_candidates)
    permit_pool = load_pipeline_permits(session, profile, limit=max_candidates // 2)
    award_pool: list[NormalizedOpportunity] = []
    if kind == "construction":
        award_pool = load_intelligence_awards(session, company_id, profile, limit=max_candidates // 2)

    all_rejections: list[dict[str, Any]] = []

    active_items, active_stats, active_rej = _process_pool(
        cip, tender_pool, "active", bps_threshold=active_threshold, include_rejections=include_rejections
    )
    all_rejections.extend(active_rej)

    pipeline_items, pipeline_stats, pipe_rej = _process_pool(
        cip, permit_pool, "pipeline", bps_threshold=PIPELINE_BPS_THRESHOLD, include_rejections=include_rejections
    )
    all_rejections.extend(pipe_rej)

    intel_items, intel_stats, intel_rej = _process_pool(
        cip,
        award_pool,
        "intelligence",
        bps_threshold=INTEL_BPS_THRESHOLD,
        active_items=active_items,
        include_rejections=include_rejections,
    )
    all_rejections.extend(intel_rej)

    growth_items, growth_stats, growth_rej = _process_pool(
        cip, tender_pool, "growth", bps_threshold=GROWTH_BPS_THRESHOLD, include_rejections=include_rejections
    )
    all_rejections.extend(growth_rej)

    relationship_items: list[dict[str, Any]] = []
    if kind == "construction":
        for partner in cip.architect_partners[:relationship_limit]:
            name = partner.get("name", "")
            count = int(partner.get("project_count", 0))
            related = _related_tender_ids(active_items, name)
            if not related and count < 2:
                continue
            scored = score_relationship(
                profile,
                entity_type="architect",
                entity_name=name,
                project_count=count,
                related_tender_count=len(related),
            )
            if scored.score >= 60:
                relationship_items.append(
                    {
                        "item_type": "relationship",
                        "subtype": "architect_partner",
                        "entity_name": name,
                        "project_count": count,
                        "score": scored.score,
                        "score_label": scored.score_label,
                        "rank_key": scored.rank_key,
                        "related_active_tender_ids": related,
                        "explanation": scored.to_explanation_dict(),
                        "reasons": scored.reasons,
                        "source": "rules",
                        "payload": {"name": name, "project_count": count, "entity_type": "architect"},
                    }
                )
        for client in cip.repeat_clients[:relationship_limit]:
            related = _related_tender_ids(active_items, client)
            if not related:
                continue
            scored = score_relationship(
                profile,
                entity_type="client",
                entity_name=client,
                project_count=cip.award_count,
                related_tender_count=len(related),
            )
            if scored.score >= 60 and len(relationship_items) < relationship_limit:
                relationship_items.append(
                    {
                        "item_type": "relationship",
                        "subtype": "repeat_client",
                        "entity_name": client,
                        "project_count": cip.award_count,
                        "score": scored.score,
                        "score_label": scored.score_label,
                        "rank_key": scored.rank_key,
                        "related_active_tender_ids": related,
                        "explanation": scored.to_explanation_dict(),
                        "reasons": scored.reasons,
                        "source": "rules",
                        "payload": {"name": client, "entity_type": "client"},
                    }
                )

    total_shown = (
        len(active_items[:active_limit])
        + len(pipeline_items[:pipeline_limit])
        + len(intel_items[:intel_limit])
        + len(relationship_items[:relationship_limit])
        + len(growth_items[:growth_limit])
    )

    result: dict[str, Any] = {
        "company_id": company_id,
        "kind": kind,
        "engine_version": "business_fit_v3",
        "company_intelligence_profile": _cip_summary(cip),
        "capability_profile": _cip_summary(cip),
        "active_opportunities": _section(
            "Active Opportunities",
            "Open tenders passing business-fit gates — pursue now",
            active_threshold,
            active_stats,
            active_items[:active_limit],
            empty_reason=_empty_reason("active", cip, active_items),
        ),
        "market_pipeline": _section(
            "Market Pipeline",
            "Permits signalling future demand in your sectors and geography",
            PIPELINE_BPS_THRESHOLD,
            pipeline_stats,
            pipeline_items[:pipeline_limit],
            empty_reason=_empty_reason("pipeline", cip, pipeline_items),
        ),
        "competitive_intelligence": _section(
            "Competitive Intelligence",
            "Actionable award intelligence linked to your active pursuits",
            INTEL_BPS_THRESHOLD,
            intel_stats,
            intel_items[:intel_limit],
            empty_reason=_empty_reason("intel", cip, intel_items),
        ),
        "relationship_opportunities": _section(
            "Relationship Opportunities",
            "Partners and clients connected to active opportunities",
            60,
            {"scanned": len(cip.architect_partners) + len(cip.repeat_clients), "gate_rejected": 0, "bps_rejected": 0, "scored": len(relationship_items), "shown": len(relationship_items)},
            relationship_items[:relationship_limit],
        ),
        "growth_opportunities": _section(
            "Growth Opportunities",
            "Expansion only where history supports it",
            GROWTH_BPS_THRESHOLD,
            growth_stats,
            growth_items[:growth_limit],
            empty_reason=_empty_reason("growth", cip, growth_items),
        ),
        "summary": {
            "total_shown": min(total_shown, GLOBAL_CAP),
            "active_bps_threshold": active_threshold,
        },
    }
    if include_rejections:
        result["rejections"] = all_rejections[:25]
    return result


def _empty_reason(section: str, cip: CompanyIntelligenceProfile, items: list) -> str | None:
    if items:
        return None
    messages = {
        "active": f"No active tenders passed business-fit gates for {cip.dominant_sector} {cip.primary_trade.replace('_', ' ')} work in your geography.",
        "pipeline": "No permit signals met quality threshold in your sectors and service areas.",
        "intel": "No contract awards linked to active pursuits or known clients.",
        "growth": "No expansion opportunities supported by company history.",
    }
    return messages.get(section)


def _cip_summary(cip: CompanyIntelligenceProfile) -> dict[str, Any]:
    return {
        "primary_trade": cip.primary_trade,
        "secondary_trades": cip.secondary_trades,
        "trade_tags": [cip.primary_trade, *cip.secondary_trades],
        "trade_confidence": cip.specialization_confidence,
        "specialization_confidence": cip.specialization_confidence,
        "company_type": cip.company_type,
        "entity_class": cip.entity_class,
        "dominant_sector": cip.dominant_sector,
        "sector_focus": cip.sector_focus,
        "work_orientation": cip.work_orientation,
        "geographic_reach": cip.geographic_reach,
        "avg_project_value": cip.typical_project_value,
        "avg_award_value": cip.value_range.median,
        "value_range": cip.value_range.to_dict(),
        "market_segments": cip.market_segments,
        "service_cities": cip.service_cities[:5],
        "project_types": cip.normalized_project_types[:6],
        "delivery_types": cip.delivery_types[:6],
        "project_clusters": [c.to_dict() for c in cip.project_clusters[:4]],
        "profile_completeness": cip.profile_completeness,
        "own_permit_count": cip.own_permit_count,
        "award_count": cip.award_count,
        "growth_direction": cip.growth_direction,
    }


def _section(
    label: str,
    description: str,
    threshold: int,
    stats: dict[str, int],
    items: list[dict[str, Any]],
    empty_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "description": description,
        "threshold": threshold,
        "total_scanned": stats.get("scanned", 0),
        "total_gate_rejected": stats.get("gate_rejected", 0),
        "total_bps_rejected": stats.get("bps_rejected", 0),
        "total_scored": stats.get("scored", 0),
        "total_shown": stats.get("shown", len(items)),
        "total_candidates_evaluated": stats.get("scanned", 0),
        "total_passed_filter": stats.get("shown", len(items)),
        "items": items,
        "empty_reason": empty_reason,
    }
