"""Orchestrator — benchmark, peers, and threat scores in one response."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from db.models import ArchCompany, Company
from pipeline.cip_builder import get_cip
from pipeline.competitive_intel.benchmark import compute_benchmark_strip
from pipeline.competitive_intel.cohort import build_market_cohort
from pipeline.competitive_intel.peers import select_top_competitors
from pipeline.competitive_intel.types import Kind

ENGINE_VERSION = "competitive_intel_v1"
WARN_INSUFFICIENT = "insufficient_market_data"


def _clamp_peer_limit(peer_limit: int) -> int:
    return max(3, min(5, peer_limit))


def _load_cips_for_members(
    session: Session,
    members: list,
    *,
    kind: Kind,
    refresh: bool,
) -> dict[int, Any]:
    cips: dict[int, Any] = {}
    for member in members:
        cips[member.id] = get_cip(session, company_id=member.id, kind=kind, refresh=refresh)
    return cips


def get_competitive_intelligence(
    session: Session,
    *,
    company_id: int,
    kind: Kind = "construction",
    peer_limit: int = 5,
    refresh_cip: bool = False,
) -> dict[str, Any]:
    peer_limit = _clamp_peer_limit(peer_limit)
    model = ArchCompany if kind == "architecture" else Company
    subject = session.get(model, company_id)
    if subject is None:
        raise ValueError(f"Company {company_id} not found")

    subject_cip = get_cip(session, company_id=company_id, kind=kind, refresh=refresh_cip)
    cohort = build_market_cohort(session, subject, subject_cip, kind=kind)

    member_ids = {subject.id, *(m.id for m in cohort.members)}
    peer_cips = _load_cips_for_members(
        session,
        [m for m in cohort.members if m.id in member_ids],
        kind=kind,
        refresh=False,
    )
    peer_cips[subject.id] = subject_cip

    peers = select_top_competitors(
        session,
        subject=subject,
        subject_cip=subject_cip,
        cohort=cohort,
        peer_cips=peer_cips,
        kind=kind,
        peer_limit=peer_limit,
    )

    benchmark = compute_benchmark_strip(subject, cohort, peers, kind=kind)

    warnings: list[str] = []
    if len(peers) < 3:
        warnings.append(WARN_INSUFFICIENT)

    top_competitors = [
        {
            "company_id": p.company_id,
            "name": p.name,
            "company_kind": p.company_kind,
            "threat_score": p.threat_score,
            "threat_breakdown": p.threat_breakdown,
            "similarity": p.similarity,
            "total_projects": p.total_projects,
            "total_value": p.total_value,
            "award_count": p.award_count,
        }
        for p in peers
    ]

    return {
        "company_id": company_id,
        "kind": kind,
        "engine_version": ENGINE_VERSION,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "market": {
            "definition": cohort.definition,
            "definition_key": cohort.definition_key,
            "cohort_size": cohort.cohort_size,
            "data_scope": cohort.data_scope,
        },
        "benchmark": benchmark,
        "top_competitors": top_competitors,
        "warnings": warnings,
    }
