"""Unified opportunity feed (PR-DISCOVERY-1): construction hybrid tenders
(dual provenance with BD business pursuit) plus permit and contract-award
signals, already scored and gated by ``discover_opportunities`` in
``pipeline.opportunity_discovery`` -- this module never re-scores or
re-thresholds anything, it only decides what already-qualifying items are
surfaced and in what order.

Merge safety: items are never deduplicated by a bare numeric id. A
composite identity key -- ``(item_type, source_table/source, id)`` -- is
used everywhere a merge or a final dedup pass happens, because permit,
contract-award, and tender rows live in separate database tables and can
share the same autoincrement id.

Ordering: within each type, ranking is deterministic (tender keeps its
existing dual-provenance interleave; permit and contract_award are sorted
by ``(-score, id)`` -- score descending, id ascending as an explicit,
stable tie-breaker). Across types, ``_interleave_by_type`` performs a
fixed-order (tender, permit, contract_award), one-item-per-type-per-round
interleave, so a long tender list can never crowd every qualifying permit
or contract-award signal out of a small ``limit`` -- as long as
``limit >= permits_available + awards_available``, all of them appear,
with the remaining slots filled by tenders.

``relationship`` items from ``pipeline.bd_recommendations`` are a
distinct recommendation type, not a project opportunity, and are never
read by this module (only ``active_opportunities`` is read from the BD
response, exactly as before).
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from pipeline.bd_recommendations import recommend_bd_intelligence
from pipeline.opportunity_discovery import discover_opportunities

Kind = Literal["construction", "architecture"]

CONSTRUCTION_SCORE_LABEL_AI = "AI Match Score"
CONSTRUCTION_SCORE_LABEL_RULES = "Construction Match Score"
CONSTRUCTION_SCORE_LABEL_PERMIT = "Permit Signal Score"
CONSTRUCTION_SCORE_LABEL_AWARD = "Contract Award Score"
BUSINESS_PURSUIT_SCORE_LABEL = "Business Pursuit Score"

OPPORTUNITY_TYPES: tuple[str, ...] = ("tender", "permit", "contract_award")


def _construction_score_label(source: str) -> str:
    return (
        CONSTRUCTION_SCORE_LABEL_AI
        if source == "ai_match"
        else CONSTRUCTION_SCORE_LABEL_RULES
    )


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
    """Tender rows from BD active_opportunities, preserving rank order.
    Only ``active_opportunities`` is read -- ``relationship_opportunities``
    (a distinct recommendation type, not a project opportunity) is never
    consulted here."""
    ranked: list[dict[str, Any]] = []
    for rank, item in enumerate(
        bd.get("active_opportunities", {}).get("items") or [], start=1
    ):
        if item.get("item_type") != "tender":
            continue
        ranked.append(
            {
                "tender_id": int(item["id"]),
                "rank": rank,
                "score": int(item["score"]),
                "score_label": str(
                    item.get("score_label") or BUSINESS_PURSUIT_SCORE_LABEL
                ),
                "pursuit_verdict": item.get("pursuit_verdict"),
                "fit_assessment": item.get("fit_assessment"),
                "reasons": list(item.get("reasons") or []),
                "explanation": item.get("explanation"),
                "payload": dict(item.get("payload") or {}),
            }
        )
    return ranked


def _extract_construction_signal(
    discovery: dict[str, Any], *, item_type: str, score_label: str
) -> list[dict[str, Any]]:
    """Permit or contract_award rows from discover_opportunities. Neither
    type has a second (BD) scoring source today, so there is no dual
    provenance to reconcile -- unlike tender, these lists are built and
    sorted here directly. Sorted by (-score, id): score descending, id
    ascending as an explicit tie-breaker, so the result is identical
    regardless of the order discover_opportunities happened to return
    matches in."""
    rows: list[dict[str, Any]] = []
    for match in discovery.get("matches") or []:
        if match.get("type") != item_type:
            continue
        rows.append(
            {
                "id": int(match["id"]),
                "score": int(match["score"]),
                "score_label": score_label,
                "source": str(match.get("source") or "rules"),
                "context": match.get("context"),
                "reasons": list(match.get("reasons") or []),
                "payload": dict(match.get("payload") or {}),
            }
        )
    rows.sort(key=lambda row: (-row["score"], row["id"]))
    return rows


def _merge_payload(
    construction: dict[str, Any] | None, business: dict[str, Any] | None
) -> dict[str, Any]:
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
        "id": tender_id,
        "tender_id": tender_id,
        "type": "tender",
        "model": model,
        "payload": _merge_payload(construction, business),
        "construction_hybrid": ch_block,
        "business_pursuit": bp_block,
    }


def _build_single_source_item(
    item_type: str, row: dict[str, Any], rank: int
) -> dict[str, Any]:
    """Build a unified item for a type with only one scoring source today
    (permit, contract_award): always model="construction_hybrid",
    business_pursuit=None. ``tender_id`` is kept (as None) purely for
    frontend/API shape backward compatibility with the tender-only
    ``ApiUnifiedOpportunityItem.tender_id`` contract; ``id`` is the
    type-agnostic identity field every consumer should use going
    forward."""
    return {
        "id": row["id"],
        "tender_id": None,
        "type": item_type,
        "model": "construction_hybrid",
        "payload": row["payload"],
        "construction_hybrid": {
            "score": row["score"],
            "score_label": row["score_label"],
            "source": row["source"],
            "context": row.get("context"),
            "rank": rank,
            "reasons": row["reasons"],
        },
        "business_pursuit": None,
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


def _interleave_by_type(
    type_lists: list[list[dict[str, Any]]], *, limit: int
) -> list[dict[str, Any]]:
    """Deterministic, type-aware interleave across already-ranked,
    already-built item lists (one list per opportunity type).

    Rule: each round takes at most one item from each list, always
    cycling through ``type_lists`` in the SAME fixed order (as passed
    in -- this module always calls it with [tender, permit,
    contract_award]); a list that is exhausted is simply skipped that
    round, without disturbing the others. Stops once ``limit`` items have
    been collected or every list is exhausted.

    This guarantees qualifying permits/awards are never crowded out by a
    long tender list: with at least one item in every list, the first
    ``len(type_lists)`` output positions already contain one of each
    type, and -- as long as ``limit >= permits_available +
    awards_available`` -- every qualifying permit/award ends up in the
    final output, with only the remaining slots filled by tenders.
    """
    ordered: list[dict[str, Any]] = []
    indices = [0] * len(type_lists)
    while len(ordered) < limit:
        progressed = False
        for i, lst in enumerate(type_lists):
            if indices[i] < len(lst):
                ordered.append(lst[indices[i]])
                indices[i] += 1
                progressed = True
                if len(ordered) >= limit:
                    break
        if not progressed:
            break
    return ordered


def _identity_key(item: dict[str, Any]) -> tuple[str, str, int]:
    """Composite identity: (item_type, source_table/source, id). Never
    dedup across different entity types or source tables by numeric id
    alone -- permit, contract_award, and tender rows come from separate
    database tables and can share the same autoincrement id."""
    item_type = item["type"]
    if item_type == "tender":
        source = str((item.get("payload") or {}).get("tender_source") or "federal")
    elif item_type == "permit":
        source = "permits"
    else:
        source = "contract_awards"
    return (item_type, source, int(item["id"]))


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
    """Unified multi-type feed: tender (dual provenance union), permit,
    and contract_award -- every item already passed the existing
    ``discover_opportunities``/``recommend_bd_intelligence`` gates and
    scoring; this function only merges, orders, and shapes what those
    already returned. See module docstring for merge-safety and
    ordering rules."""
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

    tender_ids = _interleave_tender_ids(ch_order, bp_order, limit=limit)
    tender_items = [
        _build_unified_item(tender_id, by_id_ch.get(tender_id), by_id_bp.get(tender_id))
        for tender_id in tender_ids
    ]

    permit_rows = _extract_construction_signal(
        discovery, item_type="permit", score_label=CONSTRUCTION_SCORE_LABEL_PERMIT
    )
    award_rows = _extract_construction_signal(
        discovery,
        item_type="contract_award",
        score_label=CONSTRUCTION_SCORE_LABEL_AWARD,
    )
    permit_items = [
        _build_single_source_item("permit", row, rank)
        for rank, row in enumerate(permit_rows, start=1)
    ]
    award_items = [
        _build_single_source_item("contract_award", row, rank)
        for rank, row in enumerate(award_rows, start=1)
    ]

    interleaved = _interleave_by_type(
        [tender_items, permit_items, award_items], limit=limit
    )

    items: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, int]] = set()
    model_coverage = {"construction_hybrid": 0, "business_pursuit": 0, "both": 0}
    type_coverage = {opp_type: 0 for opp_type in OPPORTUNITY_TYPES}

    for item in interleaved:
        key = _identity_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(item)
        model_coverage[item["model"]] += 1
        type_coverage[item["type"]] += 1

    return {
        "company_id": company_id,
        "kind": kind,
        "limit": limit,
        "total": len(items),
        "items": items,
        "model_coverage": model_coverage,
        "type_coverage": type_coverage,
        "sources": {
            "construction_hybrid": {
                "ranking_model": discovery.get("ranking_model"),
                "tender_count": len(construction_rows),
                "permit_count": len(permit_rows),
                "award_count": len(award_rows),
            },
            "business_pursuit": {
                "engine_version": bd.get("engine_version"),
                "tender_count": len(business_rows),
                "threshold": bd.get("active_opportunities", {}).get("threshold"),
            },
        },
    }
