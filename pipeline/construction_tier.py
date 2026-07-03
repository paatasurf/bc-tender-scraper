"""Deterministic Construction Tier Engine — evidence-only, no LLM / Google.

Pipeline stages (single responsibility each):
  1. Construction Evidence
  2. Score Calculation
  3. Tier Assignment (includes enterprise gate evaluation)
  4. Persist Results
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Float, case, func, select
from sqlalchemy.orm import Session

from db.company_analytics import company_analytics_entity_filter
from db.company_canonical_constants import ENTITY_ROLE_CANONICAL, ENTITY_ROLE_STANDALONE
from db.construction_tier_constants import (
    TIER_A,
    TIER_B,
    TIER_C,
    TIER_D,
    TIER_E,
    TIER_LABELS,
    TIER_LETTER_MAP,
)
from db.models import Company, Permit, TenderMatch, TenderOutcome
from pipeline.construction_geography import GeographicPresence, derive_geographic_presence
from pipeline.construction_tier_config import (
    AWARD_WEIGHT,
    BATCH_SIZE,
    CONSTRUCTION_SCORE_MAX,
    CONSTRUCTION_SCORE_MIN,
    CONSTRUCTION_TIER_VERSION,
    GEOGRAPHY_WEIGHT,
    LONGEVITY_WEIGHT,
    PERMIT_STATS_MONTHS,
    PERMIT_WEIGHT,
    RECENCY_MONTHS_INACTIVE,
    RECENCY_MONTHS_TIER_A,
    RECENCY_MONTHS_TIER_B,
    RECENCY_MONTHS_TIER_C,
    SCORE_TIER_A,
    SCORE_TIER_B,
    SCORE_TIER_C,
    SCORE_TIER_D,
    TENDER_KIND_CONSTRUCTION,
    TENDER_WEIGHT,
    TIER_A_MIN_AWARDS,
    TIER_A_MIN_PROJECTS,
    TIER_A_MIN_VALUE,
)


# ---------------------------------------------------------------------------
# Stage 1 — Construction Evidence
# ---------------------------------------------------------------------------


@dataclass
class PermitWindowStats:
    count: int = 0
    value: float = 0.0


@dataclass
class TenderStats:
    match_count: int = 0
    outcome_count: int = 0
    win_count: int = 0


@dataclass
class EvidenceIndexes:
    permits_24mo: dict[int, PermitWindowStats] = field(default_factory=dict)
    tender: dict[int, TenderStats] = field(default_factory=dict)


@dataclass
class ConstructionEvidence:
    total_projects: int
    total_value: float
    permit_count_24mo: int
    permit_value_24mo: float
    award_count: int
    total_award_value: float
    tender_match_count: int
    tender_outcome_count: int
    tender_win_count: int
    geographic_presence: GeographicPresence
    first_activity_date: str
    last_activity_date: str
    months_since_last_activity: int | None
    active_years: float


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _months_between(start: date, end: date) -> float:
    return max(0.0, (end.year - start.year) * 12 + (end.month - start.month) + (end.day - start.day) / 30.0)


def build_evidence_indexes(session: Session, *, reference_date: date) -> EvidenceIndexes:
    cutoff = (reference_date - timedelta(days=PERMIT_STATS_MONTHS * 30)).isoformat()

    permits_24mo: dict[int, PermitWindowStats] = {}
    for company_id, count, value in session.execute(
        select(
            Permit.company_id,
            func.count(Permit.id),
            func.sum(func.cast(func.nullif(Permit.project_value, ""), Float)),
        )
        .where(Permit.company_id.isnot(None), Permit.issue_date >= cutoff)
        .group_by(Permit.company_id)
    ).all():
        if company_id is not None:
            permits_24mo[int(company_id)] = PermitWindowStats(
                count=int(count or 0),
                value=float(value or 0.0),
            )

    tender: dict[int, TenderStats] = {}
    for company_id, count in session.execute(
        select(TenderMatch.company_id, func.count(TenderMatch.id))
        .where(TenderMatch.company_kind == TENDER_KIND_CONSTRUCTION)
        .group_by(TenderMatch.company_id)
    ).all():
        tender[int(company_id)] = TenderStats(match_count=int(count or 0))

    for company_id, count, wins in session.execute(
        select(
            TenderOutcome.company_id,
            func.count(TenderOutcome.id),
            func.sum(case((TenderOutcome.outcome == "win", 1), else_=0)),
        ).group_by(TenderOutcome.company_id)
    ).all():
        stats = tender.setdefault(int(company_id), TenderStats())
        stats.outcome_count = int(count or 0)
        stats.win_count = int(wins or 0)

    return EvidenceIndexes(permits_24mo=permits_24mo, tender=tender)


def gather_construction_evidence(
    company: Company,
    indexes: EvidenceIndexes,
    *,
    reference_date: date,
) -> ConstructionEvidence:
    permit_window = indexes.permits_24mo.get(company.id, PermitWindowStats())
    tender_stats = indexes.tender.get(company.id, TenderStats())

    first_date = _parse_date(company.first_project_date)
    last_date = _parse_date(company.last_project_date)
    award_last = _parse_date(company.last_award_date)

    if award_last and (last_date is None or award_last > last_date):
        last_date = award_last
    if first_date is None and award_last:
        first_date = _parse_date(company.first_award_date)

    months_since: int | None = None
    if last_date:
        months_since = int(_months_between(last_date, reference_date))

    active_years = 0.0
    if first_date and last_date and last_date >= first_date:
        active_years = _months_between(first_date, last_date) / 12.0

    geographic_presence = derive_geographic_presence(company)

    return ConstructionEvidence(
        total_projects=int(company.total_projects or 0),
        total_value=float(company.total_value or 0.0),
        permit_count_24mo=permit_window.count,
        permit_value_24mo=permit_window.value,
        award_count=int(company.award_count or 0),
        total_award_value=float(company.total_award_value or 0.0),
        tender_match_count=tender_stats.match_count,
        tender_outcome_count=tender_stats.outcome_count,
        tender_win_count=tender_stats.win_count,
        geographic_presence=geographic_presence,
        first_activity_date=company.first_project_date or company.first_award_date or "",
        last_activity_date=(last_date.isoformat() if last_date else ""),
        months_since_last_activity=months_since,
        active_years=round(active_years, 2),
    )


def has_construction_evidence(evidence: ConstructionEvidence) -> bool:
    return (
        evidence.total_projects > 0
        or evidence.award_count > 0
        or evidence.tender_match_count > 0
        or evidence.tender_outcome_count > 0
        or evidence.permit_count_24mo > 0
    )


# ---------------------------------------------------------------------------
# Stage 2 — Score Calculation
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _log_score(value: float, multiplier: float, cap: float) -> float:
    if value <= 0:
        return 0.0
    return _clamp(math.log10(value + 1.0) * multiplier, 0.0, cap)


@dataclass
class ScoreBreakdown:
    construction_score: int
    raw_score: float
    components: dict[str, dict[str, Any]]


def _score_permit_activity(evidence: ConstructionEvidence) -> tuple[float, dict[str, Any]]:
    recent_count = min(evidence.permit_count_24mo * 4.0, 20.0)
    lifetime_count = min(evidence.total_projects * 0.4, 10.0)
    lifetime_value = _log_score(evidence.total_value, 3.0, 10.0)
    score = _clamp(recent_count + lifetime_count + lifetime_value, 0.0, float(PERMIT_WEIGHT))
    return score, {
        "score": round(score, 2),
        "weight": PERMIT_WEIGHT,
        "permit_count_24mo": evidence.permit_count_24mo,
        "permit_value_24mo": round(evidence.permit_value_24mo, 2),
        "total_projects": evidence.total_projects,
        "total_value": round(evidence.total_value, 2),
    }


def _score_award_activity(evidence: ConstructionEvidence) -> tuple[float, dict[str, Any]]:
    count_score = min(evidence.award_count * 5.0, 15.0)
    value_score = _log_score(evidence.total_award_value, 2.5, 10.0)
    score = _clamp(count_score + value_score, 0.0, float(AWARD_WEIGHT))
    return score, {
        "score": round(score, 2),
        "weight": AWARD_WEIGHT,
        "award_count": evidence.award_count,
        "total_award_value": round(evidence.total_award_value, 2),
    }


def _score_tender_activity(evidence: ConstructionEvidence) -> tuple[float, dict[str, Any]]:
    match_score = min(evidence.tender_match_count * 2.0, 10.0)
    outcome_score = min(evidence.tender_outcome_count * 3.0, 5.0)
    score = _clamp(match_score + outcome_score, 0.0, float(TENDER_WEIGHT))
    return score, {
        "score": round(score, 2),
        "weight": TENDER_WEIGHT,
        "tender_match_count": evidence.tender_match_count,
        "tender_outcome_count": evidence.tender_outcome_count,
        "tender_win_count": evidence.tender_win_count,
    }


def _score_geographic_presence(evidence: ConstructionEvidence) -> tuple[float, dict[str, Any]]:
    presence = evidence.geographic_presence
    score = _clamp(presence.location_count / 3.0, 0.0, float(GEOGRAPHY_WEIGHT))
    return score, {
        "score": round(score, 2),
        "weight": GEOGRAPHY_WEIGHT,
        "location_count": presence.location_count,
        "source": presence.source,
    }


def _score_longevity(evidence: ConstructionEvidence) -> tuple[float, dict[str, Any]]:
    span_score = min(evidence.active_years, 7.0)
    recency_bonus = 0.0
    months = evidence.months_since_last_activity
    if months is not None:
        if months <= 12:
            recency_bonus = 3.0
        elif months <= 24:
            recency_bonus = 2.0
        elif months <= 36:
            recency_bonus = 1.0
    score = _clamp(span_score + recency_bonus, 0.0, float(LONGEVITY_WEIGHT))
    return score, {
        "score": round(score, 2),
        "weight": LONGEVITY_WEIGHT,
        "active_years": evidence.active_years,
        "months_since_last_activity": evidence.months_since_last_activity,
        "recency_bonus": recency_bonus,
    }


def calculate_construction_score(evidence: ConstructionEvidence) -> ScoreBreakdown:
    """Stage 2: compute 0–100 construction score from evidence."""
    permit_score, permit_detail = _score_permit_activity(evidence)
    award_score, award_detail = _score_award_activity(evidence)
    tender_score, tender_detail = _score_tender_activity(evidence)
    geo_score, geo_detail = _score_geographic_presence(evidence)
    longevity_score, longevity_detail = _score_longevity(evidence)

    raw_score = permit_score + award_score + tender_score + geo_score + longevity_score
    construction_score = int(
        round(_clamp(raw_score, CONSTRUCTION_SCORE_MIN, CONSTRUCTION_SCORE_MAX))
    )

    return ScoreBreakdown(
        construction_score=construction_score,
        raw_score=round(raw_score, 2),
        components={
            "permit_activity": permit_detail,
            "award_activity": award_detail,
            "tender_activity": tender_detail,
            "geographic_presence": geo_detail,
            "longevity": longevity_detail,
        },
    )


# ---------------------------------------------------------------------------
# Stage 3 — Enterprise Gate Evaluation
# ---------------------------------------------------------------------------


@dataclass
class EnterpriseGateResult:
    passed: bool
    reasons: list[str]


def evaluate_enterprise_gate(evidence: ConstructionEvidence) -> EnterpriseGateResult:
    """Stage 3: deterministic enterprise eligibility for tier A promotion."""
    reasons: list[str] = []
    if evidence.total_projects >= TIER_A_MIN_PROJECTS:
        reasons.append(f"total_projects>={TIER_A_MIN_PROJECTS}")
    if evidence.total_value >= TIER_A_MIN_VALUE:
        reasons.append(f"total_value>={TIER_A_MIN_VALUE}")
    if evidence.award_count >= TIER_A_MIN_AWARDS:
        reasons.append(f"award_count>={TIER_A_MIN_AWARDS}")
    return EnterpriseGateResult(passed=bool(reasons), reasons=reasons)


# ---------------------------------------------------------------------------
# Stage 4 — Tier Assignment (derived from score)
# ---------------------------------------------------------------------------


def assign_tier_from_score(
    construction_score: int,
    evidence: ConstructionEvidence,
    *,
    enterprise_gate: EnterpriseGateResult,
) -> str:
    """Stage 4: map construction_score + evidence recency to tier classification."""
    months = evidence.months_since_last_activity
    has_evidence = has_construction_evidence(evidence)

    if not has_evidence:
        return TIER_E

    if months is not None and months > RECENCY_MONTHS_INACTIVE and construction_score < SCORE_TIER_C:
        return TIER_E

    if (
        months is not None
        and months <= RECENCY_MONTHS_TIER_A
        and enterprise_gate.passed
        and (
            construction_score >= SCORE_TIER_A
            or construction_score >= SCORE_TIER_B
        )
    ):
        return TIER_A

    if construction_score >= SCORE_TIER_B and months is not None and months <= RECENCY_MONTHS_TIER_B:
        return TIER_B

    if construction_score >= SCORE_TIER_C and months is not None and months <= RECENCY_MONTHS_TIER_C:
        return TIER_C

    if construction_score >= SCORE_TIER_D or has_evidence:
        return TIER_D

    return TIER_E


# ---------------------------------------------------------------------------
# Orchestration + persistence
# ---------------------------------------------------------------------------


@dataclass
class ConstructionTierResult:
    construction_score: int
    tier: str
    components: dict[str, dict[str, Any]]
    evidence: ConstructionEvidence
    enterprise_gate: EnterpriseGateResult


def compute_construction_tier_result(
    company: Company,
    indexes: EvidenceIndexes,
    *,
    reference_date: date | None = None,
) -> ConstructionTierResult:
    today = reference_date or datetime.now(timezone.utc).date()
    evidence = gather_construction_evidence(company, indexes, reference_date=today)
    score = calculate_construction_score(evidence)
    gate = evaluate_enterprise_gate(evidence)
    tier = assign_tier_from_score(score.construction_score, evidence, enterprise_gate=gate)
    return ConstructionTierResult(
        construction_score=score.construction_score,
        tier=tier,
        components=score.components,
        evidence=evidence,
        enterprise_gate=gate,
    )


def tier_result_to_json(result: ConstructionTierResult, *, computed_at: datetime) -> dict[str, Any]:
    return {
        "kind": "construction_tier",
        "version": CONSTRUCTION_TIER_VERSION,
        "computed_at": computed_at.isoformat(),
        "construction_score": result.construction_score,
        "tier": result.tier,
        "tier_label": TIER_LABELS.get(result.tier, result.tier),
        "enterprise_gate": {
            "passed": result.enterprise_gate.passed,
            "reasons": result.enterprise_gate.reasons,
        },
        "components": result.components,
        "evidence": {
            "total_projects": result.evidence.total_projects,
            "total_value": result.evidence.total_value,
            "permit_count_24mo": result.evidence.permit_count_24mo,
            "permit_value_24mo": result.evidence.permit_value_24mo,
            "award_count": result.evidence.award_count,
            "total_award_value": result.evidence.total_award_value,
            "tender_match_count": result.evidence.tender_match_count,
            "tender_outcome_count": result.evidence.tender_outcome_count,
            "geographic_presence": {
                "location_count": result.evidence.geographic_presence.location_count,
                "source": result.evidence.geographic_presence.source,
                "locations": list(result.evidence.geographic_presence.locations[:20]),
            },
            "first_activity_date": result.evidence.first_activity_date,
            "last_activity_date": result.evidence.last_activity_date,
            "months_since_last_activity": result.evidence.months_since_last_activity,
            "active_years": result.evidence.active_years,
        },
    }


def persist_construction_tier_result(
    company: Company,
    result: ConstructionTierResult,
    *,
    computed_at: datetime,
) -> None:
    """Stage 5: write score, tier, and breakdown to the company row."""
    company.construction_score = result.construction_score
    company.company_tier = result.tier
    company.construction_tier_json = tier_result_to_json(result, computed_at=computed_at)
    company.construction_tier_at = computed_at


def compute_construction_tiers(
    session: Session,
    *,
    company_ids: list[int] | None = None,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Run full pipeline for canonical + standalone companies."""
    today = reference_date or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    indexes = build_evidence_indexes(session, reference_date=today)

    query = select(Company).where(
        company_analytics_entity_filter(),
        Company.entity_role.in_((ENTITY_ROLE_CANONICAL, ENTITY_ROLE_STANDALONE)),
    )
    if company_ids:
        query = query.where(Company.id.in_(company_ids))

    updated = 0
    tier_counts: dict[str, int] = {}
    offset = 0

    while True:
        companies = session.scalars(query.order_by(Company.id).offset(offset).limit(BATCH_SIZE)).all()
        if not companies:
            break

        for company in companies:
            result = compute_construction_tier_result(company, indexes, reference_date=today)
            persist_construction_tier_result(company, result, computed_at=now)
            tier_counts[result.tier] = tier_counts.get(result.tier, 0) + 1
            updated += 1

        session.commit()
        offset += BATCH_SIZE

    return {
        "companies_updated": updated,
        "tier_counts": tier_counts,
        "reference_date": today.isoformat(),
    }


def parse_tier_filter(tiers_param: str) -> set[str] | None:
    cleaned = (tiers_param or "").strip()
    if not cleaned or cleaned.upper() == "ALL":
        return None
    selected: set[str] = set()
    for part in cleaned.replace(" ", "").split(","):
        if not part:
            continue
        key = part.upper()
        if key in TIER_LETTER_MAP:
            selected.add(TIER_LETTER_MAP[key])
        elif part.lower() in TIER_LETTER_MAP.values():
            selected.add(part.lower())
    return selected or None


# Backward-compatible aliases for tests
TierEvidence = ConstructionEvidence
TierResult = ConstructionTierResult
compute_construction_tier = compute_construction_tier_result
