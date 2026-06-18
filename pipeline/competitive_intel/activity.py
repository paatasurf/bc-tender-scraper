"""Award and permit activity counters."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import ArchCompany, Company, ContractAward, Permit
from pipeline.company_matching import normalize_vendor_name
from pipeline.competitive_intel.types import ActivityStats, CompanyRow, Kind


def _parse_date(raw: str) -> date | None:
    cleaned = (raw or "").replace("/", "-")[:10].strip()
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None


def recency_score(last_project_date: str) -> float:
    parsed = _parse_date(last_project_date)
    if parsed is None:
        return 0.0
    days = (date.today() - parsed).days
    if days < 0:
        days = 0
    return max(0.0, min(100.0, 100.0 - (days / 365.0) * 100.0))


def cohort_p90(values: list[int]) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    index = int(0.9 * (len(ordered) - 1))
    return max(1.0, float(ordered[index]))


def buyer_overlap_bonus(clients_a: list[str], clients_b: list[str]) -> float:
    set_a = {c.strip().lower() for c in clients_a if c and c.strip()}
    set_b = {c.strip().lower() for c in clients_b if c and c.strip()}
    if not set_a or not set_b:
        return 0.0
    union = set_a | set_b
    return (len(set_a & set_b) / len(union)) * 20.0


def award_count_90d(session: Session, company_id: int, *, kind: Kind) -> int:
    if kind == "architecture":
        return 0
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    return int(
        session.scalar(
            select(func.count())
            .select_from(ContractAward)
            .where(
                ContractAward.company_id == company_id,
                ContractAward.award_date >= cutoff,
            )
        )
        or 0
    )


def permit_count_90d(session: Session, normalized_name: str, *, cap: int = 500) -> int:
    if not normalized_name:
        return 0
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    count = 0
    scanned = 0
    for permit in session.scalars(
        select(Permit).where(Permit.applicant != "").order_by(Permit.id.desc()).limit(cap)
    ).all():
        scanned += 1
        if normalize_vendor_name(permit.applicant) != normalized_name:
            continue
        issue = (permit.issue_date or "").replace("/", "-")[:10]
        if issue and issue >= cutoff:
            count += 1
    return count


def build_activity_stats(
    session: Session,
    members: list[CompanyRow],
    *,
    kind: Kind,
    scan_permits: bool = False,
    permit_scan_ids: set[int] | None = None,
) -> ActivityStats:
    award_counts: list[int] = []
    permit_counts: list[int] = []
    award_by_id: dict[int, int] = {}
    permit_by_id: dict[int, int] = {}

    for member in members:
        mid = member.id
        a_count = award_count_90d(session, mid, kind=kind)
        award_by_id[mid] = a_count
        award_counts.append(a_count)

        p_count = 0
        if scan_permits and permit_scan_ids and mid in permit_scan_ids:
            norm = normalize_vendor_name(member.name)
            p_count = permit_count_90d(session, norm)
        permit_by_id[mid] = p_count
        if scan_permits and permit_scan_ids and mid in permit_scan_ids:
            permit_counts.append(p_count)

    return ActivityStats(
        award_90d_p90=cohort_p90(award_counts),
        permit_90d_p90=cohort_p90(permit_counts) if permit_counts else 1.0,
        award_90d_by_company=award_by_id,
        permit_90d_by_company=permit_by_id,
    )


def award_activity_raw(
    *,
    peer_id: int,
    subject_clients: list[str],
    peer_clients: list[str],
    stats: ActivityStats,
    kind: Kind,
) -> tuple[float, str]:
    if kind == "architecture":
        return 0.0, "N/A — awards not tracked for architecture firms"

    count = stats.award_90d_by_company.get(peer_id, 0)
    normalized = min(100.0, 100.0 * count / stats.award_90d_p90)
    bonus = buyer_overlap_bonus(subject_clients, peer_clients)
    raw = min(100.0, normalized + bonus)
    detail = f"{count} award(s) in last 90d"
    if bonus > 0:
        detail += "; shared buyer history"
    if count == 0:
        detail = "No linked awards in last 90d"
    return raw, detail


def permit_activity_raw(
    *,
    peer: CompanyRow,
    peer_id: int,
    stats: ActivityStats,
    include_permit_scan: bool,
) -> tuple[float, str]:
    recency = recency_score(getattr(peer, "last_project_date", "") or "")
    permit_90d = stats.permit_90d_by_company.get(peer_id, 0) if include_permit_scan else 0
    if include_permit_scan and permit_90d > 0:
        norm = min(100.0, 100.0 * permit_90d / stats.permit_90d_p90)
        raw = min(100.0, 0.5 * norm + 0.5 * recency)
        detail = f"{permit_90d} permit(s) in 90d; last project {peer.last_project_date or 'unknown'}"
    else:
        raw = recency * 0.5 + min(50.0, recency * 0.5)
        if not include_permit_scan:
            raw = recency
        detail = f"Recency from last project {peer.last_project_date or 'unknown'}"
    return raw, detail
