"""Early permit signals — application-date lead time from Vancouver Open Data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchCompany, Company, EarlySignalEvent, Permit
from pipeline.opportunity_discovery import (
    CompanySignals,
    _company_keywords,
    _keyword_points,
    _overlap_points,
    _parse_value,
    _score_architecture_permit,
    _score_construction_permit,
)
from pipeline.scoring.explain import BreakdownFactor

Kind = Literal["construction", "architecture"]
SignalType = Literal[
    "permit_application",
    "development_permit_application",
    "rezoning_application",
]
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MIN_SCORE = 50
DATA_SCOPE = "vancouver_permits_and_early_signal_events"

EVENT_SIGNAL_TYPES = (
    "development_permit_application",
    "rezoning_application",
)

SIGNAL_TYPE_LABELS: dict[str, str] = {
    "permit_application": "Permit application",
    "development_permit_application": "Development permit application",
    "rezoning_application": "Rezoning application",
}


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


def _event_matches_regions(event: EarlySignalEvent, regions: list[str]) -> bool:
    if not regions:
        return True
    blob = f"{event.region or ''} {event.municipality or ''}".lower()
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


def _event_haystack(event: EarlySignalEvent) -> str:
    return " ".join(
        filter(
            None,
            [
                event.property_type,
                event.region,
                event.municipality,
                SIGNAL_TYPE_LABELS.get(event.signal_type, event.signal_type),
            ],
        )
    )


def _score_early_signal_event(
    signals: CompanySignals | None,
    event: EarlySignalEvent,
) -> tuple[int, list[str]]:
    haystack = _event_haystack(event)
    base = 55 if event.signal_type == "rezoning_application" else 50
    reasons = [SIGNAL_TYPE_LABELS.get(event.signal_type, "Early market signal")]

    if signals is None:
        if event.region:
            reasons.append(f"Region: {event.region}")
        return min(100, base + (5 if event.region else 0)), reasons

    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(haystack, signals.project_types, 20)
    loc_pts, loc_matched = _overlap_points(
        haystack,
        signals.neighborhoods + [signals.google_address],
        15,
    )
    score = min(100, base + kw_pts + cat_pts + loc_pts)
    if cat_matched:
        reasons.append(f"Project type fit: {', '.join(cat_matched[:3])}")
    if loc_matched:
        reasons.append(f"Area overlap: {', '.join(loc_matched[:3])}")
    if kw_matched:
        reasons.append(f"Trade keyword match: {', '.join(kw_matched[:3])}")
    elif event.region:
        reasons.append(f"Region: {event.region}")
    return score, reasons


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


def _event_breakdown_from_score(score: int, reasons: list[str]) -> list[dict[str, Any]]:
    base_points = min(35, max(0, score - 10))
    region_points = min(25, max(0, score - base_points))
    factors = [
        BreakdownFactor("signal_fit", "Signal relevance", base_points, 35, "; ".join(reasons[:2])),
        BreakdownFactor("region_fit", "Region fit", region_points, 25, reasons[-1] if reasons else ""),
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


def _event_payload(event: EarlySignalEvent, *, score: int, reasons: list[str]) -> dict[str, Any]:
    observed = ""
    if event.scraped_at:
        observed = event.scraped_at.astimezone(timezone.utc).date().isoformat()
    application_date = event.transaction_date or observed
    return {
        "id": event.id,
        "signal_type": event.signal_type,
        "external_id": event.external_id,
        "city": event.municipality or "Vancouver",
        "local_area": event.region or "",
        "title": event.property_type or SIGNAL_TYPE_LABELS.get(event.signal_type, "Early signal"),
        "address": "",
        "permit_type": event.property_type or "",
        "estimated_value": None,
        "application_date": application_date,
        "issue_date": "",
        "contractor": "",
        "applicant": "",
        "pipeline_lag_days": None,
        "score": score,
        "reasons": reasons,
        "breakdown": _event_breakdown_from_score(score, reasons),
    }


def _load_company_signals(
    session: Session,
    *,
    company_id: int | None,
    kind: Kind,
) -> tuple[CompanySignals | None, Any]:
    if company_id is None:
        return None, None

    if kind == "architecture":
        company = session.get(ArchCompany, company_id)
        if company is None:
            raise ValueError(f"Architecture company {company_id} not found")
        return CompanySignals.from_arch_company(company), company

    company = session.get(Company, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")
    return CompanySignals.from_company(company), company


def _collect_permit_signals(
    session: Session,
    *,
    since: str,
    signals_model: CompanySignals | None,
    kind: Kind,
    profile_regions: list[str],
    min_value: float | None,
    max_value: float | None,
    min_score: int,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    query = (
        select(Permit)
        .where(Permit.source == "vancouver")
        .where(Permit.application_date != "")
        .where(Permit.application_date >= since)
        .order_by(Permit.application_date.desc(), Permit.id.desc())
        .limit(fetch_limit)
    )
    rows = session.scalars(query).all()
    score_fn = (
        _score_architecture_permit if kind == "architecture" else _score_construction_permit
    )

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
    return scored


def _collect_event_signals(
    session: Session,
    *,
    since_dt: datetime,
    signals_model: CompanySignals | None,
    profile_regions: list[str],
    min_score: int,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    query = (
        select(EarlySignalEvent)
        .where(EarlySignalEvent.signal_type.in_(EVENT_SIGNAL_TYPES))
        .where(EarlySignalEvent.scraped_at >= since_dt)
        .order_by(EarlySignalEvent.scraped_at.desc(), EarlySignalEvent.id.desc())
        .limit(fetch_limit)
    )
    rows = session.scalars(query).all()

    scored: list[dict[str, Any]] = []
    for event in rows:
        if profile_regions and not _event_matches_regions(event, profile_regions):
            continue
        score, reasons = _score_early_signal_event(signals_model, event)
        if score < min_score:
            continue
        scored.append(_event_payload(event, score=score, reasons=reasons))
    return scored


def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
    return (int(row.get("score") or 0), str(row.get("application_date") or ""))


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
    since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    fetch_limit = max(limit * 8, 120)

    signals_model, _company = _load_company_signals(session, company_id=company_id, kind=kind)
    profile_regions = list(regions or [])

    permit_signals = _collect_permit_signals(
        session,
        since=since,
        signals_model=signals_model,
        kind=kind,
        profile_regions=profile_regions,
        min_value=min_value,
        max_value=max_value,
        min_score=min_score,
        fetch_limit=fetch_limit,
    )
    event_signals = _collect_event_signals(
        session,
        since_dt=since_dt,
        signals_model=signals_model,
        profile_regions=profile_regions,
        min_score=min_score,
        fetch_limit=fetch_limit,
    )

    merged = permit_signals + event_signals
    merged.sort(key=_sort_key, reverse=True)
    matches = merged[:limit]

    type_counts = {
        signal_type: sum(1 for row in matches if row.get("signal_type") == signal_type)
        for signal_type in (
            "permit_application",
            "development_permit_application",
            "rezoning_application",
        )
    }

    return {
        "data_scope": DATA_SCOPE,
        "lookback_days": lookback_days,
        "company_id": company_id,
        "kind": kind,
        "total": len(matches),
        "signal_types": type_counts,
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
