"""Top competitor selection — similarity pre-score then threat ranking."""

from __future__ import annotations

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.competitive_intel.activity import build_activity_stats
from pipeline.competitive_intel.cohort import filter_peer_candidates
from pipeline.competitive_intel.overlap import (
    category_overlap_raw,
    geographic_overlap_raw,
    similarity_pre_score,
    value_overlap_raw,
)
from pipeline.competitive_intel.threat_score import compute_threat_score
from pipeline.competitive_intel.types import (
    CompanyRow,
    Kind,
    MarketCohort,
    PeerCandidate,
    TopCompetitor,
    company_display_name,
)
from sqlalchemy.orm import Session


def rank_by_similarity(
    candidates: list[CompanyRow],
    *,
    subject: CompanyRow,
    subject_cip: CompanyIntelligenceProfile,
    peer_cips: dict[int, CompanyIntelligenceProfile],
    limit: int = 20,
) -> list[PeerCandidate]:
    scored: list[PeerCandidate] = []
    for peer in candidates:
        peer_cip = peer_cips[peer.id]
        geo_raw, _ = geographic_overlap_raw(subject_cip, peer_cip, subject, peer)
        cat_raw, _ = category_overlap_raw(subject_cip, peer_cip, subject, peer)
        val_raw, _ = value_overlap_raw(subject_cip, peer_cip, subject, peer)
        sim = similarity_pre_score(geo_raw, cat_raw, val_raw)
        scored.append(
            PeerCandidate(
                company_id=peer.id,
                name=company_display_name(peer),
                row=peer,
                cip=peer_cip,
                similarity=sim / 100.0,
                category_overlap_raw=cat_raw,
                geographic_overlap_raw=geo_raw,
                value_overlap_raw=val_raw,
            )
        )
    # Company id is the final tie-break so equal similarity scores do not
    # depend on database/input iteration order.
    scored.sort(key=lambda p: (-p.similarity, p.company_id))
    return scored[:limit]


def select_top_competitors(
    session: Session,
    *,
    subject: CompanyRow,
    subject_cip: CompanyIntelligenceProfile,
    cohort: MarketCohort,
    peer_cips: dict[int, CompanyIntelligenceProfile],
    kind: Kind,
    peer_limit: int,
) -> list[TopCompetitor]:
    apply_geo = cohort.definition_key == "sector_only_widened"
    candidates = filter_peer_candidates(
        cohort,
        subject_id=subject.id,
        subject_cip=subject_cip,
        subject=subject,
        peer_cips=peer_cips,
        apply_geo_gate=apply_geo,
    )
    top_similar = rank_by_similarity(
        candidates,
        subject=subject,
        subject_cip=subject_cip,
        peer_cips=peer_cips,
        limit=20,
    )

    stats = build_activity_stats(session, cohort.members, kind=kind, scan_permits=False)

    ranked: list[tuple[PeerCandidate, int, dict]] = []
    for peer in top_similar:
        threat = compute_threat_score(
            subject=subject,
            peer=peer.row,
            subject_cip=subject_cip,
            peer_cip=peer.cip,
            kind=kind,
            stats=stats,
            include_permit_scan=False,
        )
        ranked.append((peer, threat.score, threat.to_explanation_dict()))

    ranked.sort(
        key=lambda item: (
            -item[1],
            -(item[0].row.total_value or 0),
            item[0].company_id,
        )
    )
    preliminary = ranked[:peer_limit]

    final_ids = {item[0].company_id for item in preliminary[:5]}
    stats_with_permits = build_activity_stats(
        session,
        cohort.members,
        kind=kind,
        scan_permits=True,
        permit_scan_ids=final_ids,
    )

    results: list[TopCompetitor] = []
    for peer, _, _ in preliminary:
        threat = compute_threat_score(
            subject=subject,
            peer=peer.row,
            subject_cip=subject_cip,
            peer_cip=peer.cip,
            kind=kind,
            stats=stats_with_permits,
            include_permit_scan=peer.company_id in final_ids,
        )
        award_count: int | None = int(getattr(peer.row, "award_count", 0) or 0)
        if kind == "architecture":
            award_count = None
        results.append(
            TopCompetitor(
                company_id=peer.company_id,
                name=company_display_name(peer.row),
                company_kind=kind,
                threat_score=threat.score,
                threat_breakdown=threat.to_explanation_dict(),
                similarity=round(peer.similarity, 3),
                total_projects=int(peer.row.total_projects or 0),
                total_value=float(peer.row.total_value or 0),
                award_count=award_count,
            )
        )

    if kind == "construction":
        # Defense-in-depth layer 3: the final guard on the actual
        # get_top_competitors_for_company output. Every upstream cohort
        # layer already excludes person-like rows, but this check is
        # independent of entity_role and re-applied to the assembled
        # TopCompetitor.name here -- if any earlier layer were ever
        # bypassed or a future code path fed a person-like row into
        # `results`, it still cannot reach the caller (and, by extension,
        # Potential opportunity gaps / Competitor fit-scoring coverage,
        # which both source their peer list from this function).
        results = [c for c in results if not is_probable_person_name(c.name)]

    results.sort(key=lambda c: (-c.threat_score, -c.total_value, c.company_id))
    return results
