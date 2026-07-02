"""Unified opportunity feed — construction hybrid + BD business pursuit (union with provenance)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from pipeline.bd_recommendations import recommend_bd_intelligence
from pipeline.opportunity_discovery import discover_opportunities

Kind = Literal["construction", "architecture"]

CONSTRUCTION_SCORE_LABEL_AI = "AI Match Score"
CONSTRUCTION_SCORE_LABEL_RULES = "Construction Match Score"
BUSINESS_PURSUIT_SCORE_LABEL = "Business Pursuit Score"


def _construction_score_label(source: str) -> str:
    return CONSTRUCTION_SCORE_LABEL_AI if source == "ai_match" else CONSTRUCTION_SCORE_LABEL_RULES


def _extract_construction_tenders(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    """Tender rows from discover_opportunities, preserving API rank order."""
    ranked: list[dict[str, Any]] = []
    rank = 0
    for match in discovery.get("matches") or []:
        if match.get("type") != "tender":
            continue
        rank += 1
        tender_id = int(match["id"])
        source = str(match.get("source") or "rules")
        ranked.append(
            {
                "tender_id": tender_id,
                "rank": rank,
                "score": int(match["score"]),
                "score_label": _construction_score_label(source),
                "source": source,
                "context": match.get("context"),
                "reasons": list(match.get("reasons") or []),
                "breakdown": match.get("breakdown"),
                "payload": dict(match.get("payload") or {}),
            }
        )
    return ranked


def _extract_bd_active_tenders(bd: dict[str, Any]) -> list[dict[str, Any]]:
    """Tender rows from BD active_opportunities, preserving rank order."""
    ranked: list[dict[str, Any]] = []
    for rank, item in enumerate(bd.get("active_opportunities", {}).get("items") or [], start=1):
        if item.get("item_type") != "tender":
            continue
        ranked.append(
            {
                "tender_id": int(item["id"]),
                "rank": rank,
                "score": int(item["score"]),
                "score_label": str(item.get("score_label") or BUSINESS_PURSUIT_SCORE_LABEL),
                "pursuit_verdict": item.get("pursuit_verdict"),
                "fit_assessment": item.get("fit_assessment"),
                "reasons": list(item.get("reasons") or []),
                "explanation": item.get("explanation"),
                "payload": dict(item.get("payload") or {}),
            }
        )
    return ranked


def _merge_payload(construction: dict[str, Any] | None, business: dict[str, Any] | None) -> dict[str, Any]:
    if construction and construction.get("payload"):
        return dict(construction["payload"])
    if business and business.get("payload"):
        return dict(business["payload"])
    return {}


def _build_unified_item(
    tender_id: int,
    construction: dict[str, Any] | None,
    business: dict[str, Any] | None,
) -> dict[str, Any]:
    if construction and business:
        model = "both"
    elif construction:
        model = "construction_hybrid"
    else:
        model = "business_pursuit"

    ch_block = None
    if construction:
        ch_block = {
            "score": construction["score"],
            "score_label": construction["score_label"],
            "source": construction["source"],
            "context": construction.get("context"),
            "rank": construction["rank"],
            "reasons": construction["reasons"],
        }
        if construction.get("breakdown") is not None:
            ch_block["breakdown"] = construction["breakdown"]

    bp_block = None
    if business:
        bp_block = {
            "score": business["score"],
            "score_label": business["score_label"],
            "rank": business["rank"],
            "pursuit_verdict": business.get("pursuit_verdict"),
            "fit_assessment": business.get("fit_assessment"),
            "reasons": business["reasons"],
        }
        if business.get("explanation") is not None:
            bp_block["explanation"] = business["explanation"]

    return {
        "tender_id": tender_id,
        "type": "tender",
        "model": model,
        "payload": _merge_payload(construction, business),
        "construction_hybrid": ch_block,
        "business_pursuit": bp_block,
    }


def _interleave_tender_ids(
    construction_ranked: list[int],
    business_ranked: list[int],
    *,
    limit: int,
) -> list[int]:
    """Alternate rank positions from each model's list; skip duplicate tender_ids."""
    ordered: list[int] = []
    seen: set[int] = set()
    ci = bi = 0

    while len(ordered) < limit:
        added = False

        if ci < len(construction_ranked):
            tid = construction_ranked[ci]
            ci += 1
            if tid not in seen:
                ordered.append(tid)
                seen.add(tid)
                added = True
                if len(ordered) >= limit:
                    break

        if bi < len(business_ranked):
            tid = business_ranked[bi]
            bi += 1
            if tid not in seen:
                ordered.append(tid)
                seen.add(tid)
                added = True
                if len(ordered) >= limit:
                    break

        if not added and ci >= len(construction_ranked) and bi >= len(business_ranked):
            break

    return ordered


def get_unified_opportunities(
    session: Session,
    company_id: int,
    kind: Kind = "construction",
    limit: int = 20,
    *,
    construction_fetch_limit: int = 50,
    construction_min_score: int = 0,
    bd_active_limit: int = 10,
    include_closed: bool = False,
) -> dict[str, Any]:
    """Merge construction hybrid discovery with BD active tenders (union, dual provenance)."""
    discovery = discover_opportunities(
        company_id=company_id,
        kind=kind,
        min_score=construction_min_score,
        limit=construction_fetch_limit,
        include_closed=include_closed,
    )
    bd = recommend_bd_intelligence(
        session,
        company_id=company_id,
        kind=kind,
        active_limit=bd_active_limit,
        include_closed=include_closed,
    )

    construction_rows = _extract_construction_tenders(discovery)
    business_rows = _extract_bd_active_tenders(bd)

    by_id_ch = {row["tender_id"]: row for row in construction_rows}
    by_id_bp = {row["tender_id"]: row for row in business_rows}

    ch_order = [row["tender_id"] for row in construction_rows]
    bp_order = [row["tender_id"] for row in business_rows]

    ordered_ids = _interleave_tender_ids(ch_order, bp_order, limit=limit)

    items: list[dict[str, Any]] = []
    coverage = {"construction_hybrid": 0, "business_pursuit": 0, "both": 0}

    for tender_id in ordered_ids:
        item = _build_unified_item(
            tender_id,
            by_id_ch.get(tender_id),
            by_id_bp.get(tender_id),
        )
        items.append(item)
        coverage[item["model"]] += 1

    return {
        "company_id": company_id,
        "kind": kind,
        "limit": limit,
        "total": len(items),
        "items": items,
        "model_coverage": coverage,
        "sources": {
            "construction_hybrid": {
                "ranking_model": discovery.get("ranking_model"),
                "tender_count": len(construction_rows),
            },
            "business_pursuit": {
                "engine_version": bd.get("engine_version"),
                "tender_count": len(business_rows),
                "threshold": bd.get("active_opportunities", {}).get("threshold"),
            },
        },
    }
