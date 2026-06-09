from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import anthropic
import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key, get_env
from db.models import ArchTender, CommercialTender, Company, Permit, Tender

CLAUDE_MODEL = "claude-sonnet-4-5"
REQUEST_DELAY_SECONDS = 0.5
DEFAULT_GOOGLE_BATCH_LIMIT = 50
DEFAULT_AI_BATCH_LIMIT = 50
UPSERT_BATCH_SIZE = 500
MAX_LIST_ITEMS = 15

GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_FIELD_MASK = (
    "places.rating,places.userRatingCount,places.formattedAddress,places.nationalPhoneNumber"
)

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


def populate_companies_from_permits(session: Session) -> int:
    """Aggregate the permits table into one companies row per unique applicant."""
    print("[Companies] Aggregating permits by applicant...")
    stats: dict[str, _CompanyStats] = {}

    rows = session.execute(
        select(
            Permit.applicant,
            Permit.permit_type,
            Permit.project_value,
            Permit.issue_date,
            Permit.address,
        )
    ).yield_per(1000)

    for applicant, permit_type, project_value, issue_date, address in rows:
        name = (applicant or "").strip()
        if not name:
            continue

        entry = stats.setdefault(name, _CompanyStats())
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

    print(f"[Companies] Found {len(stats)} unique companies")

    payload = [
        {
            "name": name,
            "total_projects": entry.total_projects,
            "total_value": round(entry.total_value, 2),
            "avg_project_value": round(entry.total_value / entry.total_projects, 2)
            if entry.total_projects
            else 0.0,
            "project_types": [item for item, _ in entry.project_types.most_common(MAX_LIST_ITEMS)],
            "neighborhoods": [item for item, _ in entry.neighborhoods.most_common(MAX_LIST_ITEMS)],
            "first_project_date": entry.first_project_date,
            "last_project_date": entry.last_project_date,
        }
        for name, entry in stats.items()
    ]

    table = Company.__table__
    upserted = 0
    for start in range(0, len(payload), UPSERT_BATCH_SIZE):
        batch = payload[start : start + UPSERT_BATCH_SIZE]
        stmt = insert(table).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={column: stmt.excluded[column] for column in STATS_COLUMNS}
            | {"updated_at": func.now()},
        )
        session.execute(stmt)
        session.commit()
        upserted += len(batch)

    print(f"[Companies] Upserted {upserted} companies")
    return upserted


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
        print("[Companies] Skipping Google enrichment: GOOGLE_PLACES_API_KEY is not set.")
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
    organization = getattr(tender, "company", None) or getattr(tender, "organization", "") or ""
    value = getattr(tender, "value", None) or getattr(tender, "estimated_value", "") or ""
    deadline = getattr(tender, "deadline", None) or getattr(tender, "closing_date", "") or ""
    return f"""Title: {tender.title}
Organization: {organization}
Category: {tender.category}
Value: {value or "Not stated"}
Deadline: {deadline or "Not stated"}
Source table: {tender.__tablename__}"""


def match_company_to_tender(company: Company, tender: MatchableTender) -> dict[str, Any]:
    """Score how well a company's permit track record fits a tender. Returns
    {"match_score": int 0-100, "analysis": str}."""
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Assess how well this Vancouver construction company matches the tender below.

COMPANY:
{_company_profile_lines(company)}

TENDER:
{_tender_lines(tender)}

Consider project type fit, typical project value vs tender value, track record depth, and reputation.
Return JSON only:
{{
  "match_score": <integer 0-100>,
  "analysis": "<2-3 sentences explaining the match quality>"
}}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    payload = _extract_json(text_blocks[0])
    score = max(0, min(100, int(payload.get("match_score", 0))))
    analysis = str(payload.get("analysis", "")).strip()
    return {"match_score": score, "analysis": analysis}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_company_intelligence(session: Session) -> dict[str, int]:
    populated = populate_companies_from_permits(session)
    google_enriched = enrich_companies_google(session)
    ai_analyzed = analyze_companies_ai(session)
    return {
        "companies_populated": populated,
        "companies_google_enriched": google_enriched,
        "companies_ai_analyzed": ai_analyzed,
    }
