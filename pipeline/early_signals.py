"""Market early signals — development and rezoning activity in a company's regions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchCompany, ClientProfile, Company, EarlySignalEvent, Permit
from db.permit_lifecycle_constants import apply_active_permit_filter
from pipeline.opportunity_discovery import (
    CompanySignals,
    _company_operating_geo,
    _is_street_level_text,
    _overlap_points,
    _parse_value,
    _score_architecture_permit,
    _score_construction_permit,
)
from pipeline.scoring.explain import BreakdownFactor
from shared.datetime_utils import parse_iso_date

Kind = Literal["construction", "architecture"]
SignalType = Literal[
    "permit_application",
    "development_permit_application",
    "rezoning_application",
]
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MIN_SCORE = 50
DATA_SCOPE = "market_early_signal_events"

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
    return parse_iso_date(raw)


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


def _matches_project_types(haystack: str, project_types: list[str]) -> bool:
    if not project_types:
        return True
    _, matched = _overlap_points(haystack, project_types, 1)
    return bool(matched)


def _event_matches_project_types(event: EarlySignalEvent, project_types: list[str]) -> bool:
    return _matches_project_types(_event_haystack(event), project_types)


def _permit_haystack(permit: Permit) -> str:
    return " ".join(
        filter(
            None,
            [permit.permit_type, permit.description, permit.local_area, permit.city],
        )
    )


def _permit_matches_project_types(permit: Permit, project_types: list[str]) -> bool:
    return _matches_project_types(_permit_haystack(permit), project_types)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        if not raw:
            continue
        key = raw.strip()
        if not key:
            continue
        lower = key.lower()
        if lower in seen:
            continue
        seen.add(lower)
        out.append(key)
    return out


def _resolve_market_regions(
    session: Session,
    *,
    company_id: int | None,
    signals_model: CompanySignals | None,
    explicit_regions: list[str] | None,
) -> list[str]:
    """City and neighborhood regions for market-signal filtering.

    Street-level permit neighborhoods (e.g. "W 41ST AVENUE") rarely match
    early_signal_events municipality/region fields. Prefer gazetteer cities
    parsed from primary_city, google_address, and geographic_reach.
    """
    candidates: list[str] = list(explicit_regions or [])

    if signals_model is not None:
        cities, _geo_regions = _company_operating_geo(signals_model)
        candidates.extend(cities)

        for neighborhood in signals_model.neighborhoods:
            if neighborhood and not _is_street_level_text(str(neighborhood)):
                candidates.append(neighborhood)
        for area in signals_model.houzz_service_areas:
            if area and not _is_street_level_text(str(area)):
                candidates.append(area)

    if company_id is not None:
        profiles = session.scalars(
            select(ClientProfile).where(ClientProfile.company_id == company_id)
        ).all()
        for profile in profiles:
            candidates.extend(profile.regions or [])

    return _dedupe_strings(candidates)


def _resolve_market_project_types(
    session: Session,
    *,
    company_id: int | None,
    signals_model: CompanySignals | None,
) -> list[str]:
    """Project types / specializations used to filter market opportunities."""
    candidates: list[str] = []
    if signals_model is not None:
        candidates.extend(signals_model.project_types)
        candidates.extend(signals_model.award_categories)
        candidates.extend(signals_model.houzz_project_types)
    if company_id is not None:
        profiles = session.scalars(
            select(ClientProfile).where(ClientProfile.company_id == company_id)
        ).all()
        for profile in profiles:
            candidates.extend(profile.specializations or [])
    return _dedupe_strings(candidates)


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
    *,
    market_project_types: list[str] | None = None,
) -> tuple[int, list[str]]:
    haystack = _event_haystack(event)
    base = 55 if event.signal_type == "rezoning_application" else 50
    reasons = [SIGNAL_TYPE_LABELS.get(event.signal_type, "Market opportunity")]

    project_types = market_project_types or (signals.project_types if signals else [])
    _, cat_matched = _overlap_points(haystack, project_types, 20)
    if cat_matched:
        reasons.append(f"Project type fit: {', '.join(cat_matched[:3])}")
    elif event.property_type:
        reasons.append(event.property_type[:120])

    if event.region:
        reasons.append(f"Region: {event.region}")
    elif event.municipality:
        reasons.append(f"Municipality: {event.municipality}")

    score = min(100, base + (10 if cat_matched else 0) + (5 if event.region else 0))
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
    scraped_at = ""
    if event.scraped_at:
        scraped_at = event.scraped_at.astimezone(timezone.utc).isoformat()
        observed = event.scraped_at.astimezone(timezone.utc).date().isoformat()
    application_date = event.transaction_date or observed
    value = _parse_value(getattr(event, "project_value", "") or "")
    title = event.property_type or SIGNAL_TYPE_LABELS.get(event.signal_type, "Early signal")
    if event.address:
        title = event.address
    return {
        "id": event.id,
        "signal_type": event.signal_type,
        "external_id": event.external_id,
        "city": event.municipality or "Vancouver",
        "local_area": event.region or "",
        "title": title,
        "address": event.address or "",
        "permit_type": event.property_type or "",
        "estimated_value": value if value > 0 else None,
        "application_date": application_date,
        "scraped_at": scraped_at,
        "issue_date": "",
        "contractor": "",
        "applicant": event.applicant or "",
        "url_link": getattr(event, "url_link", "") or "",
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
    market_regions: list[str],
    market_project_types: list[str],
    min_value: float | None,
    max_value: float | None,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    query = apply_active_permit_filter(
        select(Permit)
        .where(Permit.source == "vancouver")
        .where(Permit.application_date != "")
        .where(Permit.application_date >= since)
        .order_by(Permit.application_date.desc(), Permit.id.desc())
        .limit(fetch_limit),
        include_inactive=False,
    )
    rows = session.scalars(query).all()
    score_fn = (
        _score_architecture_permit if kind == "architecture" else _score_construction_permit
    )

    scored: list[dict[str, Any]] = []
    for permit in rows:
        if market_regions and not _permit_matches_regions(permit, market_regions):
            continue
        if not _permit_matches_project_types(permit, market_project_types):
            continue
        if not _permit_matches_value_band(permit, min_value=min_value, max_value=max_value):
            continue

        if signals_model is not None:
            score, reasons = score_fn(signals_model, permit, own=False)
        else:
            value = _parse_value(permit.project_value)
            score = min(100, 45 + (15 if value >= 250_000 else 0))
            reasons = ["Recent permit application in your market"]
            lag = pipeline_lag_days(permit)
            if lag:
                reasons.append(f"{lag}-day application-to-issue pipeline")

        payload = _signal_payload(permit, score=score, reasons=reasons)
        payload["scraped_at"] = (
            permit.scraped_at.astimezone(timezone.utc).isoformat()
            if permit.scraped_at
            else permit.application_date
        )
        scored.append(payload)
    return scored


def _collect_event_signals(
    session: Session,
    *,
    since_dt: datetime,
    signals_model: CompanySignals | None,
    market_regions: list[str],
    market_project_types: list[str],
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
        if market_regions and not _event_matches_regions(event, market_regions):
            continue
        if not _event_matches_project_types(event, market_project_types):
            continue
        score, reasons = _score_early_signal_event(
            signals_model,
            event,
            market_project_types=market_project_types,
        )
        scored.append(_event_payload(event, score=score, reasons=reasons))
    return scored


def _market_sort_key(row: dict[str, Any]) -> str:
    return str(row.get("scraped_at") or row.get("application_date") or "")


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
    market_regions = _resolve_market_regions(
        session,
        company_id=company_id,
        signals_model=signals_model,
        explicit_regions=regions,
    )
    market_project_types = _resolve_market_project_types(
        session,
        company_id=company_id,
        signals_model=signals_model,
    )

    permit_signals = _collect_permit_signals(
        session,
        since=since,
        signals_model=signals_model,
        kind=kind,
        market_regions=market_regions,
        market_project_types=market_project_types,
        min_value=min_value,
        max_value=max_value,
        fetch_limit=fetch_limit,
    )
    event_signals = _collect_event_signals(
        session,
        since_dt=since_dt,
        signals_model=signals_model,
        market_regions=market_regions,
        market_project_types=market_project_types,
        fetch_limit=fetch_limit,
    )

    merged = permit_signals + event_signals
    merged.sort(key=_market_sort_key, reverse=True)
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
        "market_regions": market_regions,
        "market_project_types": market_project_types,
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
