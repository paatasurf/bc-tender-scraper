"""Missed opportunities and competitor tender activity (Feature 008).

``get_missed_opportunities`` (PR-MO1A) reports a *potential opportunity
gap*, not a proven missed bid: a peer company matching a closed tender at
a strong fit score is evidence that tender was a plausible fit for the
subject's peer group -- it is not evidence that the peer (or the
subject) actually bid, won, or otherwise participated. See
``EVIDENCE_DISCLAIMER`` and the per-item ``evidence_status``/
``participation_status``/``subject_fit_status`` fields, which exist
specifically so nothing in this response can be read as a confirmed
outcome.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CommercialTender, Tender, TenderMatch
from pipeline.ai_matching import HYBRID_AI_CANDIDATE_LIMIT
from pipeline.competitive_intel.service import get_top_competitors_for_company
from pipeline.competitive_intel.types import Kind, TopCompetitor
from pipeline.scoring.construction_match_scoring import _tender_value

LOOKBACK_DAYS = 90
STRONG_FIT_MIN_SCORE = 65
MISSED_LIMIT = 25

# PR-MO1A: potential-opportunity-gap contract for get_missed_opportunities.
SEMANTICS_VERSION = "potential_opportunity_gap_v1"
MAX_COMPETITORS_PER_ITEM = 3
EVIDENCE_DISCLAIMER = (
    "A peer company matching a closed tender does not prove that peer bid, "
    "won, or otherwise participated in it -- and it does not prove the "
    "analyzed company did not bid. This reflects fit-scoring evidence only "
    "(a strong peer-tender match), never a confirmed bid, win, or "
    "participation outcome for any company named here."
)

# PR-MARKET-1A: honest evidence contract for get_competitor_tender_activity.
# `candidate_limit` reuses the existing hybrid-matching candidate cap
# (pipeline.ai_matching.HYBRID_AI_CANDIDATE_LIMIT) that bounds how many
# tender candidates get scored/cached per company by the pipeline that
# populates TenderMatch -- never a value invented or hardcoded here.
COMPETITOR_EVIDENCE_STATUS = "scored_fit_cache_not_participation"
COMPETITOR_EVIDENCE_DISCLAIMER = (
    "This reflects fit-scoring evidence from the tender-match cache only -- "
    "a strong score means the tender was a plausible fit for this company, "
    "never a confirmed bid, win, or participation outcome."
)


class TenderActivityError(ValueError):
    """Raised for structurally invalid arguments to
    ``get_missed_opportunities`` -- currently only a wrong-typed or naive
    ``as_of`` -- raised once, up front, before any query is issued."""


def _cutoff_utc() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)


def _lookback_cutoff(as_of: datetime) -> datetime:
    """Lookback window boundary for ``get_missed_opportunities``, anchored
    to the single resolved ``as_of`` instant -- never to wall-clock "now"
    the way ``_cutoff_utc`` is. A historical/fixed ``as_of`` must always
    reproduce the same window on every call, regardless of when the call
    actually runs. ``get_competitor_tender_activity`` has no ``as_of``
    concept and keeps using ``_cutoff_utc()`` unchanged."""
    return as_of - timedelta(days=LOOKBACK_DAYS)


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
        for row in session.scalars(
            select(Tender).where(Tender.id.in_(federal_ids))
        ).all():
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


def _exclude_subject_peer(
    peers: list[TopCompetitor], company_id: int
) -> list[TopCompetitor]:
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


def _resolve_as_of(as_of: datetime | None) -> datetime:
    """Resolve the single, batch-wide ``as_of`` instant used to decide
    which tenders count as closed. Computed at most once, in UTC, by the
    caller (or here, exactly once, if omitted) -- never re-read per
    tender. A caller-supplied value must be timezone-aware; a naive
    datetime or any non-datetime raises ``TenderActivityError`` before
    any query is issued."""
    if as_of is None:
        return datetime.now(timezone.utc)
    if not isinstance(as_of, datetime):
        raise TenderActivityError(
            f"as_of must be a datetime, got {type(as_of).__name__}"
        )
    if as_of.tzinfo is None:
        raise TenderActivityError("as_of must be timezone-aware, got a naive datetime")
    return as_of


def _tender_is_closed_as_of(
    tender: Tender | CommercialTender, *, as_of: datetime
) -> bool:
    """A tender counts as closed, for this endpoint, only when
    ``is_open`` is False *and* (if ``closing_at`` is populated) that
    deadline is not later than ``as_of``. An open tender, or a
    future-deadline tender, is excluded entirely -- never surfaced as a
    "missed" opportunity while it is still live."""
    if getattr(tender, "is_open", True):
        return False
    closing_at = getattr(tender, "closing_at", None)
    if closing_at is not None:
        if closing_at.tzinfo is None:
            closing_at = closing_at.replace(tzinfo=timezone.utc)
        if closing_at > as_of:
            return False
    return True


def _top_competitor_sort_key(entry: dict[str, Any]) -> tuple[int, int, int]:
    """Deterministic ordering within a duplicate-tender group:
    competitor_threat_score DESC, match_score DESC,
    competitor_company_id ASC as the final tie-break. Sorting ascending
    on this tuple yields exactly that order."""
    return (
        -(entry["competitor_threat_score"] or 0),
        -(entry["match_score"] or 0),
        entry["competitor_company_id"],
    )


def _competitor_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "competitor_company_id": entry["competitor_company_id"],
        "competitor_name": entry["competitor_name"],
        "competitor_threat_score": entry["competitor_threat_score"],
        "match_score": entry["match_score"],
    }


def _empty_missed_opportunities_response(
    company_id: int, *, as_of: datetime
) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "lookback_days": LOOKBACK_DAYS,
        "min_match_score": STRONG_FIT_MIN_SCORE,
        "as_of": as_of.isoformat(),
        "items": [],
        "semantics_version": SEMANTICS_VERSION,
        "evidence_disclaimer": EVIDENCE_DISCLAIMER,
        "unique_tender_count": 0,
        "raw_peer_match_count": 0,
    }


def get_missed_opportunities(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    peer_limit: int = 5,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Potential-opportunity-gap report: closed tenders where at least
    one strong-fit peer was matched and the subject was not, within the
    last ``LOOKBACK_DAYS``.

    This is evidence of peer fit only -- see ``EVIDENCE_DISCLAIMER`` and
    the module docstring. Never treat an item here as a confirmed missed
    bid: ``participation_status`` is always ``"unknown"`` and
    ``subject_fit_status`` always reads as an absence of recent match
    evidence for the subject, not an absence of participation.

    Exactly one response item per ``(tender_source, tender_id)``,
    regardless of how many peers matched it -- grouping happens before
    ``MISSED_LIMIT`` is applied, so five peers sharing one tender can
    never consume five of the limited slots. Within a group, the "top"
    competitor (surfacing in the legacy ``competitor_company_id``/
    ``competitor_name``/``competitor_threat_score``/``match_score``
    fields) is chosen deterministically -- see
    ``_top_competitor_sort_key``. Up to ``MAX_COMPETITORS_PER_ITEM``
    competitors for the group are also listed in ``competitors``.

    Only closed tenders are ever included (``_tender_is_closed_as_of``);
    a tender row missing from the database entirely is excluded outright
    -- this endpoint never fabricates a synthetic ``"Tender #<id>"``
    placeholder the way ``get_competitor_tender_activity`` does. The
    lookback window itself is anchored to ``resolved_as_of`` (see
    ``_lookback_cutoff``), not to wall-clock "now", so a fixed/historical
    ``as_of`` always reproduces the same window.

    Two response-level counters, deliberately measured at different
    pipeline stages:

    - ``raw_peer_match_count``: the number of raw strong-fit
      ``TenderMatch`` rows for the peer group within the lookback window
      (``score >= STRONG_FIT_MIN_SCORE``) -- counted *before* subject-match
      exclusion, non-construction-source filtering, or tender open/closed
      state is applied. This is peer-match volume, not tender count, and
      is never deduped by tender or by competitor.
    - ``unique_tender_count``: the number of distinct eligible tenders
      *after* every filter (subject exclusion, source eligibility,
      missing-tender exclusion, closed-tender-as-of-``resolved_as_of``)
      has been applied, but *before* ``MISSED_LIMIT`` truncates ``items``.
      This is the true count of grouped opportunities, independent of the
      response page size.
    """
    resolved_as_of = _resolve_as_of(as_of)

    if kind != "construction":
        return _empty_missed_opportunities_response(company_id, as_of=resolved_as_of)

    peers = get_top_competitors_for_company(
        session, company_id=company_id, kind=kind, peer_limit=peer_limit
    )
    if not peers:
        return _empty_missed_opportunities_response(company_id, as_of=resolved_as_of)

    cutoff = _lookback_cutoff(resolved_as_of)
    peer_ids = [peer.company_id for peer in peers]
    threat_by_id, name_by_id = _peer_maps(peers)
    subject_keys = _subject_match_keys(
        session, company_id=company_id, kind=kind, cutoff=cutoff
    )

    competitor_matches = session.scalars(
        select(TenderMatch)
        .where(
            TenderMatch.company_kind == kind,
            TenderMatch.company_id.in_(peer_ids),
            TenderMatch.created_at >= cutoff,
            TenderMatch.score >= STRONG_FIT_MIN_SCORE,
        )
        .order_by(
            TenderMatch.score.desc(),
            TenderMatch.created_at.desc(),
            TenderMatch.id.asc(),
        )
    ).all()

    # Raw strong-fit peer-match row count -- measured here, before subject
    # exclusion / source filtering / tender-state filtering. See the
    # docstring above for why this is deliberately not the same number as
    # unique_tender_count.
    raw_peer_match_count = len(competitor_matches)

    # Group by (tender_source, tender_id) first -- subject's own recent
    # matches and non-construction-eligible sources are dropped up front,
    # before grouping, so they never contribute a phantom group.
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for match in competitor_matches:
        key = _tender_key(match.tender_source, match.tender_id)
        if key in subject_keys:
            continue
        if match.tender_source not in {"federal", "commercial"}:
            continue
        groups.setdefault(key, []).append(
            {
                "competitor_company_id": match.company_id,
                "competitor_name": name_by_id.get(match.company_id, ""),
                "competitor_threat_score": threat_by_id.get(match.company_id, 0),
                "match_score": int(match.score or 0),
            }
        )

    # Batch-load every candidate tender once (never per-match) -- needed
    # to check is_open/closing_at before a group is ever surfaced.
    tender_cache = _load_construction_tenders(session, set(groups.keys()))

    items: list[dict[str, Any]] = []
    for key, entries in groups.items():
        tender_source, tender_id = key
        tender = tender_cache.get(key)
        if tender is None:
            continue  # missing tender row -- excluded entirely, no synthetic title
        if not _tender_is_closed_as_of(tender, as_of=resolved_as_of):
            continue

        # Defensive dedupe by competitor within the group (keeps the
        # best-ranked entry per company_id if a peer somehow matched the
        # same tender more than once).
        by_company: dict[int, dict[str, Any]] = {}
        for entry in entries:
            existing = by_company.get(entry["competitor_company_id"])
            if existing is None or _top_competitor_sort_key(
                entry
            ) < _top_competitor_sort_key(existing):
                by_company[entry["competitor_company_id"]] = entry
        deduped_entries = sorted(by_company.values(), key=_top_competitor_sort_key)
        top = deduped_entries[0]

        items.append(
            {
                "tender_source": tender_source,
                "tender_id": tender_id,
                "title": (getattr(tender, "title", "") or "").strip()
                or f"Tender #{tender_id}",
                "tender_value": round(_tender_value(tender, tender_source), 2),
                "competitor_company_id": top["competitor_company_id"],
                "competitor_name": top["competitor_name"],
                "competitor_threat_score": top["competitor_threat_score"],
                "match_score": top["match_score"],
                "competitor_count": len(deduped_entries),
                "competitors": [
                    _competitor_summary(entry)
                    for entry in deduped_entries[:MAX_COMPETITORS_PER_ITEM]
                ],
                "opportunity_status": "potential_gap",
                "evidence_status": "peer_fit_only",
                "participation_status": "unknown",
                "subject_fit_status": "no_recent_match_evidence",
            }
        )

    # Fully deterministic final ordering: competitor_threat_score DESC,
    # tender_value DESC, then (tender_source, tender_id) ASC as the last
    # tie-break, so two items with equal threat and value never depend on
    # dict/query iteration order.
    items.sort(
        key=lambda row: (
            -(row.get("competitor_threat_score") or 0),
            -(row.get("tender_value") or 0),
            row["tender_source"],
            row["tender_id"],
        )
    )

    # Distinct eligible tenders after every filter, before MISSED_LIMIT
    # truncates items -- see the docstring above.
    unique_tender_count = len(items)

    return {
        "company_id": company_id,
        "lookback_days": LOOKBACK_DAYS,
        "min_match_score": STRONG_FIT_MIN_SCORE,
        "as_of": resolved_as_of.isoformat(),
        "items": items[:MISSED_LIMIT],
        "semantics_version": SEMANTICS_VERSION,
        "evidence_disclaimer": EVIDENCE_DISCLAIMER,
        "unique_tender_count": unique_tender_count,
        "raw_peer_match_count": raw_peer_match_count,
    }


def get_competitor_tender_activity(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    peer_limit: int = 5,
) -> dict[str, Any]:
    """Per-competitor tender-match-cache activity summary.

    Every count/value here is scored fit-cache evidence, never a
    confirmed bid/win/participation outcome -- see
    ``COMPETITOR_EVIDENCE_DISCLAIMER`` and each item's
    ``evidence_status``/``evidence_disclaimer`` fields.

    Two counts, deliberately different, both present on every item:

    - ``raw_match_count`` (alias: the deprecated ``match_count``, kept
      unchanged for backward compatibility -- always numerically equal to
      ``raw_match_count``): the number of raw ``TenderMatch`` cache rows
      for this competitor within the lookback window, *before* dedup by
      tender. If the same ``(tender_source, tender_id)`` was matched more
      than once, every row is counted here.
    - ``unique_tender_match_count``: the number of distinct
      ``(tender_source, tender_id)`` pairs after dedup -- this is the
      true count of tenders this competitor was matched to.

    ``total_tender_value`` is summed over that same deduped, unique
    tender set only -- a tender is never counted twice in the total, no
    matter how many raw cache rows reference it. Dedup happens before
    every one of these final metrics is computed, so they are always
    mathematically consistent with each other.

    ``candidate_limit`` is the existing hybrid-matching candidate cap
    (``pipeline.ai_matching.HYBRID_AI_CANDIDATE_LIMIT``) that bounds how
    many tender candidates the pipeline populating ``TenderMatch`` scores
    per company -- never hardcoded here. ``candidate_limit_saturated`` is
    true when ``raw_match_count`` reaches that cap, signaling the cache
    may not hold this competitor's full match activity (there could be
    more matches than what is visible here).

    Competitors are ranked by ``unique_tender_match_count`` (not the raw
    count) DESC, then ``total_tender_value`` DESC, then ``company_id`` ASC
    as the final deterministic tie-break -- consistent with the
    dedup-before-metrics rule above.
    """
    if kind != "construction":
        return {
            "company_id": company_id,
            "lookback_days": LOOKBACK_DAYS,
            "competitors": [],
        }

    peers = get_top_competitors_for_company(
        session, company_id=company_id, kind=kind, peer_limit=peer_limit
    )
    peers = _exclude_subject_peer(peers, company_id)
    if not peers:
        return {
            "company_id": company_id,
            "lookback_days": LOOKBACK_DAYS,
            "competitors": [],
        }

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
            .order_by(
                TenderMatch.score.desc(),
                TenderMatch.created_at.desc(),
                TenderMatch.id.asc(),
            )
        ).all()

        raw_match_count = len(matches)

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

        unique_tender_match_count = len(seen_keys)

        competitors_out.append(
            {
                "company_id": peer.company_id,
                "name": peer.name,
                "threat_score": peer.threat_score,
                # Deprecated alias, kept for backward compatibility --
                # always numerically identical to raw_match_count.
                "match_count": raw_match_count,
                "raw_match_count": raw_match_count,
                "unique_tender_match_count": unique_tender_match_count,
                "total_tender_value": round(total_value, 2),
                "evidence_status": COMPETITOR_EVIDENCE_STATUS,
                "evidence_disclaimer": COMPETITOR_EVIDENCE_DISCLAIMER,
                "candidate_limit": HYBRID_AI_CANDIDATE_LIMIT,
                "candidate_limit_saturated": raw_match_count
                >= HYBRID_AI_CANDIDATE_LIMIT,
            }
        )

    competitors_out.sort(
        key=lambda row: (
            -row["unique_tender_match_count"],
            -row["total_tender_value"],
            row["company_id"],
        )
    )

    return {
        "company_id": company_id,
        "lookback_days": LOOKBACK_DAYS,
        "competitors": competitors_out,
    }
