"""Missed opportunities and competitor tender activity (Feature 008)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CommercialTender, Tender, TenderMatch
from pipeline.competitive_intel.service import get_top_competitors_for_company
from pipeline.competitive_intel.types import Kind, TopCompetitor
from pipeline.scoring.construction_match_scoring import _tender_value

LOOKBACK_DAYS = 90
STRONG_FIT_MIN_SCORE = 65
MISSED_LIMIT = 25


def _cutoff_utc() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)


def _tender_key(source: str, tender_id: int) -> tuple[str, int]:
    return (source, int(tender_id))


def _load_construction_tenders(
    session: Session,
    keys: set[tuple[str, int]],
) -> dict[tuple[str, int], Tender | CommercialTender]:
    if not keys:
        return {}

    federal_ids = [tid for source, tid in keys if source == "federal"]
    commercial_ids = [tid for source, tid in keys if source == "commercial"]

    loaded: dict[tuple[str, int], Tender | CommercialTender] = {}
    if federal_ids:
        for row in session.scalars(select(Tender).where(Tender.id.in_(federal_ids))).all():
            loaded[("federal", int(row.id))] = row
    if commercial_ids:
        for row in session.scalars(
            select(CommercialTender).where(CommercialTender.id.in_(commercial_ids))
        ).all():
            loaded[("commercial", int(row.id))] = row
    return loaded


def _tender_payload(
    session: Session,
    *,
    tender_source: str,
    tender_id: int,
    cache: dict[tuple[str, int], Tender | CommercialTender],
) -> dict[str, Any]:
    key = _tender_key(tender_source, tender_id)
    tender = cache.get(key)
    if tender is None:
        missing = _load_construction_tenders(session, {key})
        cache.update(missing)
        tender = cache.get(key)

    if tender is None:
        return {
            "tender_source": tender_source,
            "tender_id": tender_id,
            "title": f"Tender #{tender_id}",
            "tender_value": 0.0,
        }

    return {
        "tender_source": tender_source,
        "tender_id": tender_id,
        "title": (getattr(tender, "title", "") or "").strip() or f"Tender #{tender_id}",
        "tender_value": round(_tender_value(tender, tender_source), 2),
    }


def _peer_maps(peers: list[TopCompetitor]) -> tuple[dict[int, int], dict[int, str]]:
    return (
        {peer.company_id: peer.threat_score for peer in peers},
        {peer.company_id: peer.name for peer in peers},
    )


def _exclude_subject_peer(peers: list[TopCompetitor], company_id: int) -> list[TopCompetitor]:
    return [peer for peer in peers if peer.company_id != company_id]


def _subject_match_keys(
    session: Session,
    *,
    company_id: int,
    kind: Kind,
    cutoff: datetime,
) -> set[tuple[str, int]]:
    rows = session.execute(
        select(TenderMatch.tender_source, TenderMatch.tender_id).where(
            TenderMatch.company_kind == kind,
            TenderMatch.company_id == company_id,
            TenderMatch.created_at >= cutoff,
        )
    ).all()
    return {_tender_key(source, tender_id) for source, tender_id in rows}


def get_missed_opportunities(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    peer_limit: int = 5,
) -> dict[str, Any]:
    if kind != "construction":
        return {"company_id": company_id, "lookback_days": LOOKBACK_DAYS, "items": []}

    peers = get_top_competitors_for_company(
        session, company_id=company_id, kind=kind, peer_limit=peer_limit
    )
    if not peers:
        return {"company_id": company_id, "lookback_days": LOOKBACK_DAYS, "items": []}

    cutoff = _cutoff_utc()
    peer_ids = [peer.company_id for peer in peers]
    threat_by_id, name_by_id = _peer_maps(peers)
    subject_keys = _subject_match_keys(session, company_id=company_id, kind=kind, cutoff=cutoff)

    competitor_matches = session.scalars(
        select(TenderMatch)
        .where(
            TenderMatch.company_kind == kind,
            TenderMatch.company_id.in_(peer_ids),
            TenderMatch.created_at >= cutoff,
            TenderMatch.score >= STRONG_FIT_MIN_SCORE,
        )
        .order_by(TenderMatch.score.desc(), TenderMatch.created_at.desc())
    ).all()

    tender_cache: dict[tuple[str, int], Tender | CommercialTender] = {}
    items: list[dict[str, Any]] = []
    for match in competitor_matches:
        key = _tender_key(match.tender_source, match.tender_id)
        if key in subject_keys:
            continue
        if match.tender_source not in {"federal", "commercial"}:
            continue

        tender_info = _tender_payload(
            session,
            tender_source=match.tender_source,
            tender_id=match.tender_id,
            cache=tender_cache,
        )
        items.append(
            {
                **tender_info,
                "competitor_company_id": match.company_id,
                "competitor_name": name_by_id.get(match.company_id, ""),
                "competitor_threat_score": threat_by_id.get(match.company_id, 0),
                "match_score": int(match.score or 0),
            }
        )

    items.sort(
        key=lambda row: (
            row.get("competitor_threat_score") or 0,
            row.get("tender_value") or 0,
        ),
        reverse=True,
    )

    return {
        "company_id": company_id,
        "lookback_days": LOOKBACK_DAYS,
        "min_match_score": STRONG_FIT_MIN_SCORE,
        "items": items[:MISSED_LIMIT],
    }


def get_competitor_tender_activity(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    peer_limit: int = 5,
) -> dict[str, Any]:
    if kind != "construction":
        return {"company_id": company_id, "lookback_days": LOOKBACK_DAYS, "competitors": []}

    peers = get_top_competitors_for_company(
        session, company_id=company_id, kind=kind, peer_limit=peer_limit
    )
    peers = _exclude_subject_peer(peers, company_id)
    if not peers:
        return {"company_id": company_id, "lookback_days": LOOKBACK_DAYS, "competitors": []}

    cutoff = _cutoff_utc()
    tender_cache: dict[tuple[str, int], Tender | CommercialTender] = {}
    competitors_out: list[dict[str, Any]] = []

    for peer in peers[:5]:
        matches = session.scalars(
            select(TenderMatch)
            .where(
                TenderMatch.company_kind == kind,
                TenderMatch.company_id == peer.company_id,
                TenderMatch.created_at >= cutoff,
                TenderMatch.tender_source.in_(("federal", "commercial")),
            )
            .order_by(TenderMatch.score.desc(), TenderMatch.created_at.desc())
        ).all()

        total_value = 0.0
        seen_keys: set[tuple[str, int]] = set()
        for match in matches:
            key = _tender_key(match.tender_source, match.tender_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            tender_info = _tender_payload(
                session,
                tender_source=match.tender_source,
                tender_id=match.tender_id,
                cache=tender_cache,
            )
            total_value += float(tender_info.get("tender_value") or 0)

        competitors_out.append(
            {
                "company_id": peer.company_id,
                "name": peer.name,
                "threat_score": peer.threat_score,
                "match_count": len(matches),
                "total_tender_value": round(total_value, 2),
            }
        )

    competitors_out.sort(key=lambda row: (row["match_count"], row["total_tender_value"]), reverse=True)

    return {
        "company_id": company_id,
        "lookback_days": LOOKBACK_DAYS,
        "competitors": competitors_out,
    }
