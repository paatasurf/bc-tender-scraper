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

# --- PR-EARLY-3A: deterministic signal quality layer ------------------
#
# Additive on top of the existing relevance ``score``/``min_score``
# contract, which is unchanged (min_score still gates on ``score``, not
# on any of these fields). ``quality_score``/``quality_tier``/
# ``quality_reasons`` describe how early/large a project signal is, and
# control the default ranking order. This is never a substitute for
# ``score``, never AI or fuzzy matching (pure deterministic keyword/token
# overlap, reusing the same ``_overlap_points`` mechanism already used
# for project-type gating elsewhere in this module), and never implies a
# win probability, tender guarantee, or participation certainty.

QualityTier = Literal["high_potential", "market_watch", "low_priority"]

_QUALITY_TIER_HIGH_POTENTIAL_FLOOR = 70
_QUALITY_TIER_MARKET_WATCH_FLOOR = 40

_DEVELOPMENT_SIGNAL_TYPES = ("development_permit_application", "rezoning_application")

# Deterministic, explainable maintenance-scale keyword set: real work that
# is small/upkeep-scale for a *typical* general contractor (single-unit
# interior work, accessibility lifts, routine mechanical/roof/exterior
# maintenance). Never used to drop a signal from the results, only to
# lower its default rank -- and never applied when the wording overlaps
# the viewing company's own specialization (see
# ``_classify_signal_quality``).
_MAINTENANCE_SCALE_KEYWORDS: tuple[str, ...] = (
    "interior alteration",
    "single family dwelling",
    "single-family dwelling",
    "one family dwelling",
    "medical lift",
    "wheelchair lift",
    "stair lift",
    "platform lift",
    "hot water tank",
    "furnace replacement",
    "hvac replacement",
    "re-roof",
    "reroof",
    "roof repair",
    "sign permit",
    "fence permit",
    "deck repair",
    "minor alteration",
)

# Typical historical lead time from this signal type to a posted tender --
# a machine-readable pattern, not a guarantee. "confidence" reflects how
# reliably that pattern has held, not a probability of any specific
# outcome.
_LEAD_TIME_BY_TYPE: dict[str, tuple[str, str]] = {
    "rezoning_application": ("6-18 months", "low"),
    "development_permit_application": ("3-12 months", "medium"),
    "permit_application": ("1-3 months", "medium"),
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


def _matches_project_types(haystack: str, project_types: list[str]) -> bool:
    if not project_types:
        return True
    _, matched = _overlap_points(haystack, project_types, 1)
    return bool(matched)


def _event_matches_project_types(
    event: EarlySignalEvent, project_types: list[str]
) -> bool:
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


def _lead_time_for_signal(signal_type: str) -> tuple[str, str]:
    """Machine-readable typical lead-time label + confidence for this
    signal type -- describes a historical pattern, never a guarantee that
    a tender will follow. Callers (frontend) should render this as
    "typical lead-time", never as a promise."""
    return _LEAD_TIME_BY_TYPE.get(signal_type, ("1-3 months", "low"))


def _classify_signal_quality(
    *,
    signal_type: str,
    haystack: str,
    estimated_value: float | None,
    specializations: list[str],
) -> tuple[int, QualityTier, list[str]]:
    """Deterministic, explainable quality ranking layer, additive to the
    existing relevance ``score``. Pure keyword/token overlap -- never AI,
    never fuzzy entity matching, never a win-probability or
    tender-guarantee claim.

    Policy:
    - development_permit_application / rezoning_application always get a
      strong base score -- they are early, large-scale pipeline signals
      by nature, regardless of wording.
    - permit_application defaults to a moderate base score. If its text
      matches a maintenance-scale keyword (single-unit interior work,
      accessibility lifts, routine mechanical/roof/exterior maintenance),
      it is downranked for a *typical* general contractor -- UNLESS that
      same wording also matches the viewing company's own specialization
      (e.g. an electrical/solar company and a solar-related permit, or a
      renovation specialist and an interior alteration permit), in which
      case it is never unfairly downranked.
    - A present, positive estimated project value adds a bonus at two
      thresholds; a missing/zero value never subtracts anything.
    - The final 0-100 score maps to exactly one of three tiers
      (high_potential / market_watch / low_priority). low_priority
      signals are still returned -- this only affects default rank, never
      removes a signal from the result set.
    """
    reasons: list[str] = []
    text_lower = haystack.lower()

    if signal_type in _DEVELOPMENT_SIGNAL_TYPES:
        quality = 75
        reasons.append(
            f"{SIGNAL_TYPE_LABELS.get(signal_type, signal_type)} -- early, "
            "large-scale project pipeline signal"
        )
    else:
        quality = 45
        reasons.append("Building permit application")

        matched_keyword = next(
            (
                keyword
                for keyword in _MAINTENANCE_SCALE_KEYWORDS
                if keyword in text_lower
            ),
            None,
        )
        if matched_keyword is not None:
            _, specialization_matched = (
                _overlap_points(haystack, specializations, 1)
                if specializations
                else (0, [])
            )
            if specialization_matched:
                quality += 10
                reasons.append(
                    "Matches your specialization "
                    f"({', '.join(specialization_matched[:2])}) -- not downranked "
                    f"despite maintenance-scale wording ('{matched_keyword}')"
                )
            else:
                quality -= 30
                reasons.append(
                    f"Maintenance-scale wording ('{matched_keyword}') -- typically "
                    "lower priority for a general contractor"
                )

    if estimated_value is not None and estimated_value > 0:
        if estimated_value >= 5_000_000:
            quality += 15
            reasons.append("Large project value ($5M+)")
        elif estimated_value >= 1_000_000:
            quality += 8
            reasons.append("Substantial project value ($1M+)")

    quality = max(0, min(100, quality))

    if quality >= _QUALITY_TIER_HIGH_POTENTIAL_FLOOR:
        tier: QualityTier = "high_potential"
    elif quality >= _QUALITY_TIER_MARKET_WATCH_FLOOR:
        tier = "market_watch"
    else:
        tier = "low_priority"

    return quality, tier, reasons


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


def _breakdown_from_score(
    score: int, reasons: list[str], *, lag_days: int | None
) -> list[dict[str, Any]]:
    base_points = min(25, max(0, score - 15))
    lag_points = 0
    lag_detail = "Issue date recorded"
    if lag_days is not None and lag_days > 0:
        lag_points = min(15, max(5, lag_days // 4))
        lag_detail = f"{lag_days} days from application to issuance"
    region_points = min(20, max(0, score - base_points - lag_points))
    factors = [
        BreakdownFactor(
            "permit_fit", "Permit relevance", base_points, 25, "; ".join(reasons[:2])
        ),
        BreakdownFactor(
            "pipeline_lag", "Application lead time", lag_points, 15, lag_detail
        ),
        BreakdownFactor(
            "region_value",
            "Region & value fit",
            region_points,
            20,
            reasons[-1] if reasons else "",
        ),
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
        BreakdownFactor(
            "signal_fit", "Signal relevance", base_points, 35, "; ".join(reasons[:2])
        ),
        BreakdownFactor(
            "region_fit",
            "Region fit",
            region_points,
            25,
            reasons[-1] if reasons else "",
        ),
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


def _signal_payload(
    permit: Permit,
    *,
    score: int,
    reasons: list[str],
    quality_score: int,
    quality_tier: QualityTier,
    quality_reasons: list[str],
) -> dict[str, Any]:
    lag = pipeline_lag_days(permit)
    value = _parse_value(permit.project_value)
    lead_time_label, lead_time_confidence = _lead_time_for_signal("permit_application")
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
        "quality_score": quality_score,
        "quality_tier": quality_tier,
        "quality_reasons": quality_reasons,
        "lead_time_label": lead_time_label,
        "lead_time_confidence": lead_time_confidence,
    }


def _event_payload(
    event: EarlySignalEvent,
    *,
    score: int,
    reasons: list[str],
    quality_score: int,
    quality_tier: QualityTier,
    quality_reasons: list[str],
) -> dict[str, Any]:
    observed = ""
    scraped_at = ""
    if event.scraped_at:
        scraped_at = event.scraped_at.astimezone(timezone.utc).isoformat()
        observed = event.scraped_at.astimezone(timezone.utc).date().isoformat()
    application_date = event.transaction_date or observed
    value = _parse_value(getattr(event, "project_value", "") or "")
    title = event.property_type or SIGNAL_TYPE_LABELS.get(
        event.signal_type, "Early signal"
    )
    if event.address:
        title = event.address
    lead_time_label, lead_time_confidence = _lead_time_for_signal(event.signal_type)
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
        "quality_score": quality_score,
        "quality_tier": quality_tier,
        "quality_reasons": quality_reasons,
        "lead_time_label": lead_time_label,
        "lead_time_confidence": lead_time_confidence,
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
) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
        _score_architecture_permit
        if kind == "architecture"
        else _score_construction_permit
    )

    diagnostics = {"scanned": 0, "rejected_by_region": 0, "rejected_by_project_type": 0}
    scored: list[dict[str, Any]] = []
    for permit in rows:
        diagnostics["scanned"] += 1
        if market_regions and not _permit_matches_regions(permit, market_regions):
            diagnostics["rejected_by_region"] += 1
            continue
        if not _permit_matches_project_types(permit, market_project_types):
            diagnostics["rejected_by_project_type"] += 1
            continue
        if not _permit_matches_value_band(
            permit, min_value=min_value, max_value=max_value
        ):
            continue

        permit_value = _parse_value(permit.project_value)

        if signals_model is not None:
            score, reasons = score_fn(signals_model, permit, own=False)
        else:
            score = min(100, 45 + (15 if permit_value >= 250_000 else 0))
            reasons = ["Recent permit application in your market"]
            lag = pipeline_lag_days(permit)
            if lag:
                reasons.append(f"{lag}-day application-to-issue pipeline")

        quality_score, quality_tier, quality_reasons = _classify_signal_quality(
            signal_type="permit_application",
            haystack=_permit_haystack(permit),
            estimated_value=permit_value if permit_value > 0 else None,
            specializations=market_project_types,
        )

        payload = _signal_payload(
            permit,
            score=score,
            reasons=reasons,
            quality_score=quality_score,
            quality_tier=quality_tier,
            quality_reasons=quality_reasons,
        )
        payload["scraped_at"] = (
            permit.scraped_at.astimezone(timezone.utc).isoformat()
            if permit.scraped_at
            else permit.application_date
        )
        scored.append(payload)
    return scored, diagnostics


def _collect_event_signals(
    session: Session,
    *,
    since_dt: datetime,
    signals_model: CompanySignals | None,
    market_regions: list[str],
    market_project_types: list[str],
    fetch_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    query = (
        select(EarlySignalEvent)
        .where(EarlySignalEvent.signal_type.in_(EVENT_SIGNAL_TYPES))
        .where(EarlySignalEvent.scraped_at >= since_dt)
        .order_by(EarlySignalEvent.scraped_at.desc(), EarlySignalEvent.id.desc())
        .limit(fetch_limit)
    )
    rows = session.scalars(query).all()

    diagnostics = {"scanned": 0, "rejected_by_region": 0, "rejected_by_project_type": 0}
    scored: list[dict[str, Any]] = []
    for event in rows:
        diagnostics["scanned"] += 1
        if market_regions and not _event_matches_regions(event, market_regions):
            diagnostics["rejected_by_region"] += 1
            continue
        if not _event_matches_project_types(event, market_project_types):
            diagnostics["rejected_by_project_type"] += 1
            continue
        score, reasons = _score_early_signal_event(
            signals_model,
            event,
            market_project_types=market_project_types,
        )
        event_value = _parse_value(getattr(event, "project_value", "") or "")
        quality_score, quality_tier, quality_reasons = _classify_signal_quality(
            signal_type=event.signal_type,
            haystack=_event_haystack(event),
            estimated_value=event_value if event_value > 0 else None,
            specializations=market_project_types,
        )
        scored.append(
            _event_payload(
                event,
                score=score,
                reasons=reasons,
                quality_score=quality_score,
                quality_tier=quality_tier,
                quality_reasons=quality_reasons,
            )
        )
    return scored, diagnostics


def _signal_type_sort_rank(signal_type: str) -> str:
    """Lexicographic tie-break key -- stable and well-defined regardless of
    how many/which signal types exist, no arbitrary priority table needed."""
    return str(signal_type or "")


def _order_signals_deterministically(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Primary: quality_score (highest first) -- the deterministic
    project-quality ranking layer (PR-EARLY-3A), so a handful of strong
    early-project signals are never buried under a long list of
    technically-qualifying but weak/maintenance-scale ones. Then
    freshness (newest first), then relevance score (higher first), then
    signal_type (lexicographic), then id (ascending) as stable
    tie-breakers -- fully deterministic regardless of input order, since
    Python's sort is stable and each pass below is applied from least- to
    most-significant key."""
    ordered = sorted(rows, key=lambda row: int(row.get("id") or 0))
    ordered.sort(key=lambda row: _signal_type_sort_rank(row.get("signal_type")))
    ordered.sort(key=lambda row: int(row.get("score") or 0), reverse=True)
    ordered.sort(key=_market_sort_key, reverse=True)
    ordered.sort(key=lambda row: int(row.get("quality_score") or 0), reverse=True)
    return ordered


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
    """Collect, score, threshold, quality-rank, and return early market
    signals.

    ``min_score`` is enforced uniformly across every collected signal type
    (permit_application, development_permit_application,
    rezoning_application), applied after each row's existing scoring and
    before ``limit`` -- a score exactly equal to ``min_score`` is included,
    a score below it is excluded. Existing score formulas, region/
    project-type/value gates, and the lookback-day default are unchanged.

    Each signal also carries an additive, deterministic quality layer
    (PR-EARLY-3A) -- ``quality_score``, ``quality_tier``
    ("high_potential"/"market_watch"/"low_priority"), and
    ``quality_reasons`` -- describing how early/large a project signal is
    (see ``_classify_signal_quality``). This never gates which signals are
    returned (a low_priority signal is still returned, only ranked lower
    by default) and is never a win-probability, tender-guarantee, or
    participation claim. Each signal also carries a machine-readable
    ``lead_time_label``/``lead_time_confidence`` pair describing a
    historical pattern, not a promise.

    Ordering is fully deterministic: ``quality_score`` first (highest
    first), then freshness (newest first, by scraped_at/application_date),
    then relevance ``score`` (highest first), then signal_type
    (lexicographic), then id (ascending) -- the same set of input rows
    always produces the same output order, regardless of the order they
    were collected in.

    The response's ``diagnostics`` block is additive and aggregate-only
    (counts, never raw ids/names/addresses/payload/exception text):
    ``scanned_permits``, ``scanned_events`` -- rows read from each source
    before any filtering; ``rejected_by_region``, ``rejected_by_project_type``
    -- rows dropped by those existing gates (summed across both sources);
    ``rejected_by_min_score`` -- scored rows dropped by the score
    threshold; ``returned_count`` -- rows actually returned in ``signals``
    (equal to ``total``). The existing ``signals``/``signal_types``/
    ``total`` fields are unchanged in shape.
    """
    since = (
        datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    ).isoformat()
    since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    fetch_limit = max(limit * 8, 120)

    signals_model, _company = _load_company_signals(
        session, company_id=company_id, kind=kind
    )
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

    permit_signals, permit_diagnostics = _collect_permit_signals(
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
    event_signals, event_diagnostics = _collect_event_signals(
        session,
        since_dt=since_dt,
        signals_model=signals_model,
        market_regions=market_regions,
        market_project_types=market_project_types,
        fetch_limit=fetch_limit,
    )

    merged = permit_signals + event_signals
    # Score threshold applies uniformly to every collected signal type,
    # after scoring and before limit -- exactly at min_score is included,
    # below it is excluded (>=, never >).
    rejected_by_min_score = sum(
        1 for row in merged if int(row.get("score") or 0) < min_score
    )
    qualifying = [row for row in merged if int(row.get("score") or 0) >= min_score]
    ordered = _order_signals_deterministically(qualifying)
    matches = ordered[:limit]

    type_counts = {
        signal_type: sum(1 for row in matches if row.get("signal_type") == signal_type)
        for signal_type in (
            "permit_application",
            "development_permit_application",
            "rezoning_application",
        )
    }

    diagnostics = {
        "scanned_permits": permit_diagnostics["scanned"],
        "scanned_events": event_diagnostics["scanned"],
        "rejected_by_region": (
            permit_diagnostics["rejected_by_region"]
            + event_diagnostics["rejected_by_region"]
        ),
        "rejected_by_project_type": (
            permit_diagnostics["rejected_by_project_type"]
            + event_diagnostics["rejected_by_project_type"]
        ),
        "rejected_by_min_score": rejected_by_min_score,
        "returned_count": len(matches),
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
        "diagnostics": diagnostics,
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
