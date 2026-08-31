from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import anthropic
import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key, get_env
from db.models import ArchTender, CommercialTender, Company, Permit, Tender
from pipeline.company_classification import (
    classify_companies,
    compute_enrichment_status,
)
from pipeline.construction_tier import compute_construction_tiers
from pipeline.company_resolution import CompanyResolver, RESOLUTION_STATUS_REVIEW

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-5"
REQUEST_DELAY_SECONDS = 0.5
DEFAULT_GOOGLE_BATCH_LIMIT = 50
DEFAULT_AI_BATCH_LIMIT = 50
UPSERT_BATCH_SIZE = 500
MAX_LIST_ITEMS = 15

GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_FIELD_MASK = "places.rating,places.userRatingCount,places.formattedAddress,places.nationalPhoneNumber"

# Permit aggregation columns that populate_companies_from_permits owns. Google
# and AI enrichment columns are preserved across re-runs.
STATS_COLUMNS = (
    "total_projects",
    "total_value",
    "avg_project_value",
    "project_types",
    "neighborhoods",
    "first_project_date",
    "last_project_date",
)


def _batch_limit(env_name: str, default: int) -> int:
    raw = get_env(env_name, str(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _parse_value(raw: str | None) -> float:
    if not raw:
        return 0.0
    digits = re.sub(r"[^\d.]", "", str(raw))
    if not digits:
        return 0.0
    try:
        return float(digits)
    except ValueError:
        return 0.0


_ADDRESS_STREET_RE = re.compile(r"^\s*\d+[\w/-]*\s+(.+)$")


def _neighborhood_from_address(address: str | None) -> str:
    """Derive a coarse locality label (street name) from a permit address."""
    if not address:
        return ""
    street = address.split(",")[0].strip()
    match = _ADDRESS_STREET_RE.match(street)
    if match:
        street = match.group(1).strip()
    return street.upper()


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("Claude response did not contain JSON") from None
        return json.loads(match.group(0))


# ---------------------------------------------------------------------------
# Step 2: populate companies from permits
# ---------------------------------------------------------------------------


@dataclass
class _CompanyStats:
    total_projects: int = 0
    total_value: float = 0.0
    project_types: Counter = field(default_factory=Counter)
    neighborhoods: Counter = field(default_factory=Counter)
    first_project_date: str = ""
    last_project_date: str = ""


def _permit_resolution_query():
    """The SELECT driving populate_companies_from_permits()'s per-row UPDATE
    loop, ordered by Permit.id.

    This ordering is the fix for a confirmed production deadlock
    (pipeline_runs id 759, 2026-08-05: "DeadlockDetected ... while updating
    tuple (184,2) in relation 'permits'", two processes each holding a lock
    the other was waiting for). populate_companies_from_permits() has no
    concurrency guard of its own (nothing in pipeline.run_coordinator covers
    this step -- see PR #156/#157/#158's audit), so two overlapping
    invocations touching the same permit rows are possible. Without an
    explicit ORDER BY, PostgreSQL does not guarantee row order for a plain
    scan, so two concurrent transactions could acquire the same rows' locks
    in different relative orders -- a classic lock-order deadlock. With every
    invocation locking permit rows strictly in ascending id order, two
    overlapping transactions can only ever block on each other in one
    direction (whichever reaches a given id first), never form a cycle, and
    therefore cannot deadlock on these rows -- this is a lock-order fix, not
    a retry: nothing here catches or retries a deadlock, it structurally
    cannot occur across two callers of this query."""
    return select(
        Permit.id,
        Permit.applicant,
        Permit.permit_type,
        Permit.project_value,
        Permit.issue_date,
        Permit.address,
        Permit.city,
        Permit.source,
    ).order_by(Permit.id)


def populate_companies_from_permits(session: Session) -> int:
    """Aggregate permits onto canonical companies via resolve_company (not raw applicant)."""
    print("[Companies] Aggregating permits by resolved canonical company...")
    stats: dict[int, _CompanyStats] = {}
    resolver = CompanyResolver(session)
    review_count = 0
    skipped_person = 0

    rows = session.execute(_permit_resolution_query()).yield_per(1000)

    for (
        permit_id,
        applicant,
        permit_type,
        project_value,
        issue_date,
        address,
        city,
        source,
    ) in rows:
        raw = (applicant or "").strip()
        if not raw:
            continue

        resolution = resolver.resolve(
            raw,
            source=f"permits:{source or 'unknown'}",
            city=city or "",
        )
        if resolution.company_id is None:
            if resolution.status == "person_skip":
                skipped_person += 1
            continue
        if resolution.status == RESOLUTION_STATUS_REVIEW:
            review_count += 1

        company_id = int(resolution.company_id)
        entry = stats.setdefault(company_id, _CompanyStats())
        entry.total_projects += 1
        entry.total_value += _parse_value(project_value)

        permit_type = (permit_type or "").strip()
        if permit_type:
            entry.project_types[permit_type] += 1

        neighborhood = _neighborhood_from_address(address)
        if neighborhood:
            entry.neighborhoods[neighborhood] += 1

        issue_date = (issue_date or "").strip()
        if issue_date:
            if not entry.first_project_date or issue_date < entry.first_project_date:
                entry.first_project_date = issue_date
            if not entry.last_project_date or issue_date > entry.last_project_date:
                entry.last_project_date = issue_date

        session.execute(
            Permit.__table__.update()
            .where(Permit.id == int(permit_id))
            .values(
                company_id=company_id,
                canonical_merge_confidence=resolution.confidence,
                canonical_merge_method=resolution.method,
            )
        )

    session.commit()
    print(
        f"[Companies] Resolved {len(stats)} canonical companies "
        f"(review={review_count}, person_skip={skipped_person}, "
        f"confidence_log={len(resolver.confidence_log)})"
    )

    updated = 0
    # Ascending company_id, not dict insertion order -- same lock-order
    # rationale as _permit_resolution_query() above, applied to the
    # companies phase so it can't independently become a second AB-BA
    # deadlock surface between two overlapping invocations.
    for company_id, entry in sorted(stats.items()):
        session.execute(
            Company.__table__.update()
            .where(Company.id == company_id)
            .values(
                total_projects=entry.total_projects,
                total_value=round(entry.total_value, 2),
                avg_project_value=(
                    round(entry.total_value / entry.total_projects, 2)
                    if entry.total_projects
                    else 0.0
                ),
                project_types=[
                    item for item, _ in entry.project_types.most_common(MAX_LIST_ITEMS)
                ],
                neighborhoods=[
                    item for item, _ in entry.neighborhoods.most_common(MAX_LIST_ITEMS)
                ],
                first_project_date=entry.first_project_date,
                last_project_date=entry.last_project_date,
                updated_at=func.now(),
            )
        )
        updated += 1
    session.commit()

    print(f"[Companies] Updated stats on {updated} canonical companies")
    return updated


# ---------------------------------------------------------------------------
# Step 3: Google Places enrichment
# ---------------------------------------------------------------------------


def _google_api_key() -> str:
    for name in ("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY"):
        value = get_env(name)
        if value:
            return value
    return ""


def _fetch_google_place(api_key: str, company_name: str) -> dict[str, Any] | None:
    response = requests.post(
        GOOGLE_PLACES_SEARCH_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
        },
        json={"textQuery": f"{company_name} Vancouver BC Canada", "pageSize": 1},
        timeout=20,
    )
    response.raise_for_status()
    places = response.json().get("places", [])
    return places[0] if places else None


def enrich_companies_google(session: Session) -> int:
    """Fetch rating, review count, address, and phone from Google Places."""
    api_key = _google_api_key()
    if not api_key:
        print(
            "[Companies] Skipping Google enrichment: GOOGLE_PLACES_API_KEY is not set."
        )
        return 0

    limit = _batch_limit("COMPANY_GOOGLE_MAX_PER_RUN", DEFAULT_GOOGLE_BATCH_LIMIT)
    companies = session.scalars(
        select(Company)
        .where(Company.google_reviews_count.is_(None))
        .order_by(Company.total_value.desc())
        .limit(limit)
    ).all()

    enriched = 0
    for index, company in enumerate(companies, start=1):
        print(f"[Companies] Google {index}/{len(companies)}: {company.name[:70]}")
        try:
            place = _fetch_google_place(api_key, company.name)
            if place:
                company.google_rating = place.get("rating")
                company.google_reviews_count = int(place.get("userRatingCount") or 0)
                company.google_address = str(place.get("formattedAddress") or "")[:500]
                company.google_phone = str(place.get("nationalPhoneNumber") or "")[:50]
            else:
                # Mark as attempted so the next run moves on to other companies.
                company.google_reviews_count = 0
            company.last_enriched_at = datetime.now(timezone.utc)
            company.enrichment_status = compute_enrichment_status(company)
            session.commit()
            enriched += 1
        except Exception as exc:
            session.rollback()
            print(f"[Companies] Google enrichment failed: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"[Companies] Google enrichment complete: {enriched} companies")
    return enriched


# ---------------------------------------------------------------------------
# Step 4: AI analysis
# ---------------------------------------------------------------------------


def _company_profile_lines(company: Company) -> str:
    rating = (
        f"{company.google_rating} ({company.google_reviews_count} reviews)"
        if company.google_rating is not None
        else "No Google rating found"
    )
    return f"""Company: {company.name}
Total permits: {company.total_projects}
Total project value: ${company.total_value:,.0f} CAD
Average project value: ${company.avg_project_value:,.0f} CAD
Project types: {", ".join(company.project_types or []) or "Unknown"}
Areas of activity: {", ".join((company.neighborhoods or [])[:8]) or "Unknown"}
Active from {company.first_project_date or "?"} to {company.last_project_date or "?"}
Google rating: {rating}"""


def _build_analysis_prompt(company: Company) -> str:
    return f"""You are assessing a construction company that pulls building permits in Vancouver, BC.

{_company_profile_lines(company)}

Based on the project history (volume, value, span of activity) and the Google rating, return JSON only:
{{
  "reliability_score": <integer 0-100, higher = more established and reliable>,
  "summary": "<2-3 sentence company profile for a construction-industry audience>"
}}"""


def _analyze_company(client: anthropic.Anthropic, company: Company) -> tuple[int, str]:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": _build_analysis_prompt(company)}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    payload = _extract_json(text_blocks[0])
    score = max(0, min(100, int(payload.get("reliability_score", 0))))
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise ValueError("Claude response missing summary")
    return score, summary


def analyze_companies_ai(session: Session) -> int:
    """Generate ai_reliability_score and ai_summary for companies missing them."""
    api_key = get_anthropic_api_key()
    if not api_key:
        print("[Companies] Skipping AI analysis: ANTHROPIC_API_KEY is not set.")
        return 0

    client = anthropic.Anthropic(api_key=api_key)
    limit = _batch_limit("COMPANY_AI_MAX_PER_RUN", DEFAULT_AI_BATCH_LIMIT)
    companies = session.scalars(
        select(Company)
        .where(Company.ai_reliability_score.is_(None))
        .order_by(Company.total_value.desc())
        .limit(limit)
    ).all()

    analyzed = 0
    for index, company in enumerate(companies, start=1):
        print(f"[Companies] AI {index}/{len(companies)}: {company.name[:70]}")
        try:
            score, summary = _analyze_company(client, company)
            company.ai_reliability_score = score
            company.ai_summary = summary
            company.last_enriched_at = datetime.now(timezone.utc)
            company.enrichment_status = compute_enrichment_status(company)
            session.commit()
            analyzed += 1
        except Exception as exc:
            session.rollback()
            print(f"[Companies] AI analysis failed: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"[Companies] AI analysis complete: {analyzed} companies")
    return analyzed


# ---------------------------------------------------------------------------
# Step 5 support: AI tender match (used by the API)
# ---------------------------------------------------------------------------

MatchableTender = ArchTender | CommercialTender | Tender


def _tender_lines(tender: MatchableTender) -> str:
    organization = (
        getattr(tender, "company", None) or getattr(tender, "organization", "") or ""
    )
    value = (
        getattr(tender, "value", None) or getattr(tender, "estimated_value", "") or ""
    )
    deadline = (
        getattr(tender, "deadline", None) or getattr(tender, "closing_date", "") or ""
    )
    return f"""Title: {tender.title}
Organization: {organization}
Category: {tender.category}
Value: {value or "Not stated"}
Deadline: {deadline or "Not stated"}
Source table: {tender.__tablename__}"""


def match_company_to_tender(
    company: Company, tender: MatchableTender
) -> dict[str, Any]:
    """Score how well a company fits a tender and suggest a win strategy."""
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""You are a bid strategist helping a Vancouver BC construction company win a public tender.

COMPANY:
{_company_profile_lines(company)}

TENDER:
{_tender_lines(tender)}

Assess fit and provide a win strategy based on permit history, project types, value range, and reputation.

Return JSON only:
{{
  "match_score": <integer 0-100, overall fit>,
  "win_probability": <integer 0-100, realistic chance of winning this specific tender>,
  "recommendations": [
    "<short actionable tip 1>",
    "<short actionable tip 2>",
    "<short actionable tip 3>"
  ],
  "analysis": "<2-3 sentences explaining the match quality>"
}}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    payload = _extract_json(text_blocks[0])
    match_score = max(0, min(100, int(payload.get("match_score", 0))))
    win_probability = max(0, min(100, int(payload.get("win_probability", match_score))))
    recommendations = payload.get("recommendations") or []
    if not isinstance(recommendations, list):
        recommendations = []
    recommendations = [str(r).strip() for r in recommendations if str(r).strip()][:3]
    while len(recommendations) < 3:
        recommendations.append(
            "Emphasize relevant BC project experience in your bid response."
        )
    analysis = str(payload.get("analysis", "")).strip()
    return {
        "match_score": match_score,
        "win_probability": win_probability,
        "recommendations": recommendations,
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _safe_call_on_phase(on_phase: Callable[[str], None], phase: str) -> None:
    """(M3D-B) Invoke an optional progress callback without ever letting it
    affect the caller -- mirrors pipeline.ai_scoring._safe_call_on_phase()
    and pipeline.surrey_identity_scheduler._safe_call_on_phase() exactly
    (kept as a separate, small, local copy rather than a shared import so
    this module's own company-intelligence logic stays untouched by
    anything outside it). Never logs the callback's exception text --
    only a fixed, phase-named warning."""
    try:
        on_phase(phase)
    except Exception:
        logger.warning(
            "[Company Intelligence] on_phase callback failed for phase=%s", phase
        )


def run_company_intelligence(
    session: Session, *, on_phase: Callable[[str], None] | None = None
) -> dict[str, int]:
    """``on_phase``, if given, is called after each of the 5 named steps
    below (populate, google_enrich, ai_analyze, classify,
    construction_tiers) via _safe_call_on_phase() -- a failing callback is
    swallowed and logged as a fixed warning, never raised, so telemetry
    can never change this function's own steps, order, or return value.
    Defaults to None, a complete no-op -- existing callers are unaffected."""
    populated = populate_companies_from_permits(session)
    if on_phase is not None:
        _safe_call_on_phase(on_phase, "populate")

    google_enriched = enrich_companies_google(session)
    if on_phase is not None:
        _safe_call_on_phase(on_phase, "google_enrich")

    ai_analyzed = analyze_companies_ai(session)
    if on_phase is not None:
        _safe_call_on_phase(on_phase, "ai_analyze")

    classified = classify_companies(session)
    if on_phase is not None:
        _safe_call_on_phase(on_phase, "classify")

    tier_results = compute_construction_tiers(session)
    if on_phase is not None:
        _safe_call_on_phase(on_phase, "construction_tiers")

    return {
        "companies_populated": populated,
        "companies_google_enriched": google_enriched,
        "companies_ai_analyzed": ai_analyzed,
        "companies_classified": classified,
        "construction_tiers_updated": tier_results.get("companies_updated", 0),
    }
