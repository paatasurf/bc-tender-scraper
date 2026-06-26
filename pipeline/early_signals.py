"""Early permit signals — application-date lead time from Vancouver Open Data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchCompany, Company, Permit
from pipeline.opportunity_discovery import (
    CompanySignals,
    _parse_value,
    _score_architecture_permit,
    _score_construction_permit,
)
from pipeline.scoring.explain import BreakdownFactor

Kind = Literal["construction", "architecture"]
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MIN_SCORE = 50
DATA_SCOPE = "vancouver_permit_applications"


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).replace("/", "-")[:10])
    except ValueError:
        return None


def pipeline_lag_days(permit: Permit) -> int | None:
    applied = _parse_iso_date(permit.application_date)
    issued = _parse_iso_date(permit.issue_date)
    if applied and issued:
        return (issued - applied).days
    return None


def _permit_matches_regions(permit: Permit, regions: list[str]) -> bool:
    if not regions:
        return True
    blob = f"{permit.local_area or ''} {permit.city or ''}".lower()
    return any(region.lower() in blob for region in regions if region)


def _permit_matches_value_band(
    permit: Permit,
    *,
    min_value: float | None,
    max_value: float | None,
) -> bool:
    value = _parse_value(permit.project_value)
    if min_value is not None and value < min_value:
        return False
    if max_value is not None and value > max_value:
        return False
    return True


def _breakdown_from_score(score: int, reasons: list[str], *, lag_days: int | None) -> list[dict[str, Any]]:
    base_points = min(25, max(0, score - 15))
    lag_points = 0
    lag_detail = "Issue date recorded"
    if lag_days is not None and lag_days > 0:
        lag_points = min(15, max(5, lag_days // 4))
        lag_detail = f"{lag_days} days from application to issuance"
    region_points = min(20, max(0, score - base_points - lag_points))
    factors = [
        BreakdownFactor("permit_fit", "Permit relevance", base_points, 25, "; ".join(reasons[:2])),
        BreakdownFactor("pipeline_lag", "Application lead time", lag_points, 15, lag_detail),
        BreakdownFactor("region_value", "Region & value fit", region_points, 20, reasons[-1] if reasons else ""),
    ]
    total = sum(f.points for f in factors)
    if total != score and factors:
        adjust = score - total
        factors[0] = BreakdownFactor(
            factors[0].factor,
            factors[0].label,
            max(0, factors[0].points + adjust),
            factors[0].max_points,
            factors[0].detail,
        )
    return [f.to_dict() for f in factors]


def _signal_payload(permit: Permit, *, score: int, reasons: list[str]) -> dict[str, Any]:
    lag = pipeline_lag_days(permit)
    value = _parse_value(permit.project_value)
    return {
        "id": permit.id,
        "signal_type": "permit_application",
        "external_id": permit.external_id,
        "city": permit.city or "Vancouver",
        "local_area": permit.local_area or "",
        "title": permit.description or permit.permit_type or permit.address,
        "address": permit.address,
        "permit_type": permit.permit_type,
        "estimated_value": value if value > 0 else None,
        "application_date": permit.application_date or "",
        "issue_date": permit.issue_date or "",
        "contractor": permit.contractor or "",
        "applicant": permit.applicant or "",
        "pipeline_lag_days": lag,
        "score": score,
        "reasons": reasons,
        "breakdown": _breakdown_from_score(score, reasons, lag_days=lag),
    }


def get_early_signals(
    session: Session,
    *,
    company_id: int | None = None,
    kind: Kind = "construction",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_value: float | None = None,
    max_value: float | None = None,
    min_score: int = DEFAULT_MIN_SCORE,
    limit: int = 15,
    regions: list[str] | None = None,
) -> dict[str, Any]:
    since = (datetime.now(timezone.utc).date() - timedelta(days=lookback_days)).isoformat()
    query = (
        select(Permit)
        .where(Permit.source == "vancouver")
        .where(Permit.application_date != "")
        .where(Permit.application_date >= since)
        .order_by(Permit.application_date.desc(), Permit.id.desc())
        .limit(max(limit * 8, 120))
    )
    rows = session.scalars(query).all()

    signals_model = None
    score_fn = _score_construction_permit
    if company_id is not None:
        if kind == "architecture":
            company = session.get(ArchCompany, company_id)
            if company is None:
                raise ValueError(f"Architecture company {company_id} not found")
            signals_model = CompanySignals.from_arch_company(company)
            score_fn = _score_architecture_permit
        else:
            company = session.get(Company, company_id)
            if company is None:
                raise ValueError(f"Company {company_id} not found")
            signals_model = CompanySignals.from_company(company)

    profile_regions = list(regions or [])
    scored: list[dict[str, Any]] = []
    for permit in rows:
        if profile_regions and not _permit_matches_regions(permit, profile_regions):
            continue
        if not _permit_matches_value_band(permit, min_value=min_value, max_value=max_value):
            continue

        own = False
        if signals_model is not None and signals_model.normalized_name:
            applicant_key = (permit.applicant or "").lower().replace(" ", "")
            own = signals_model.normalized_name in applicant_key

        if signals_model is not None:
            score, reasons = score_fn(signals_model, permit, own=own)
        else:
            value = _parse_value(permit.project_value)
            score = min(100, 40 + (20 if value >= 250_000 else 0) + (15 if permit.contractor else 0))
            reasons = ["Recent Vancouver permit application"]
            lag = pipeline_lag_days(permit)
            if lag:
                reasons.append(f"{lag}-day application-to-issue pipeline")

        if score < min_score:
            continue
        scored.append(_signal_payload(permit, score=score, reasons=reasons))

    scored.sort(key=lambda row: (row["score"], row.get("application_date") or ""), reverse=True)
    matches = scored[:limit]

    return {
        "data_scope": DATA_SCOPE,
        "lookback_days": lookback_days,
        "company_id": company_id,
        "kind": kind,
        "total": len(matches),
        "signals": matches,
    }


def get_early_signals_for_profile(
    session: Session,
    profile: Any,
    *,
    lookback_days: int = 7,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Profile-scoped signals for email digests (region + value band)."""
    result = get_early_signals(
        session,
        company_id=int(profile.company_id),
        kind="construction",
        lookback_days=lookback_days,
        min_value=profile.min_project_value,
        max_value=profile.max_project_value,
        min_score=45,
        limit=limit,
        regions=list(profile.regions or []),
    )
    return result["signals"]
