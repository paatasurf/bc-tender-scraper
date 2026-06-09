from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import anthropic
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key
from db.models import ArchCompany, ArchTender, Permit
from pipeline.company_intelligence import (
    CLAUDE_MODEL,
    DEFAULT_AI_BATCH_LIMIT,
    DEFAULT_GOOGLE_BATCH_LIMIT,
    REQUEST_DELAY_SECONDS,
    MAX_LIST_ITEMS,
    STATS_COLUMNS,
    UPSERT_BATCH_SIZE,
    _batch_limit,
    _extract_json,
    _fetch_google_place,
    _google_api_key,
    _neighborhood_from_address,
    _parse_value,
    _tender_lines,
)

EXCLUDED_ARCHITECT_VALUES = {"", "N/A"}


# ---------------------------------------------------------------------------
# Step 2: populate arch companies from permits
# ---------------------------------------------------------------------------


@dataclass
class _ArchCompanyStats:
    total_projects: int = 0
    total_value: float = 0.0
    project_types: Counter = field(default_factory=Counter)
    neighborhoods: Counter = field(default_factory=Counter)
    first_project_date: str = ""
    last_project_date: str = ""


def populate_arch_companies_from_permits(session: Session) -> int:
    """Aggregate the permits table into one arch_companies row per unique architect."""
    print("[ArchCompanies] Aggregating permits by architect...")
    stats: dict[str, _ArchCompanyStats] = {}

    rows = session.execute(
        select(
            Permit.architect,
            Permit.permit_type,
            Permit.project_value,
            Permit.issue_date,
            Permit.address,
        ).where(
            Permit.architect.isnot(None),
            Permit.architect != "",
            Permit.architect != "N/A",
        )
    ).yield_per(1000)

    for architect, permit_type, project_value, issue_date, address in rows:
        name = (architect or "").strip()
        if not name or name in EXCLUDED_ARCHITECT_VALUES:
            continue

        entry = stats.setdefault(name, _ArchCompanyStats())
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

    print(f"[ArchCompanies] Found {len(stats)} unique architecture firms")

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

    table = ArchCompany.__table__
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

    print(f"[ArchCompanies] Upserted {upserted} architecture firms")
    return upserted


# ---------------------------------------------------------------------------
# Step 3: Google Places enrichment
# ---------------------------------------------------------------------------


def enrich_arch_companies_google(session: Session) -> int:
    """Fetch rating, review count, address, and phone from Google Places."""
    api_key = _google_api_key()
    if not api_key:
        print("[ArchCompanies] Skipping Google enrichment: GOOGLE_PLACES_API_KEY is not set.")
        return 0

    limit = _batch_limit("ARCH_COMPANY_GOOGLE_MAX_PER_RUN", DEFAULT_GOOGLE_BATCH_LIMIT)
    companies = session.scalars(
        select(ArchCompany)
        .where(ArchCompany.google_reviews_count.is_(None))
        .order_by(ArchCompany.total_value.desc())
        .limit(limit)
    ).all()

    enriched = 0
    for index, company in enumerate(companies, start=1):
        print(f"[ArchCompanies] Google {index}/{len(companies)}: {company.name[:70]}")
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
            print(f"[ArchCompanies] Google enrichment failed: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"[ArchCompanies] Google enrichment complete: {enriched} architecture firms")
    return enriched


# ---------------------------------------------------------------------------
# Step 4: AI analysis
# ---------------------------------------------------------------------------


def _arch_company_profile_lines(company: ArchCompany) -> str:
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


def _build_analysis_prompt(company: ArchCompany) -> str:
    return f"""You are assessing an architecture firm credited on building permits in Vancouver, BC.

{_arch_company_profile_lines(company)}

Based on the project history (volume, value, span of activity) and the Google rating, return JSON only:
{{
  "reliability_score": <integer 0-100, higher = more established and reliable>,
  "summary": "<2-3 sentence company profile for an architecture-industry audience>"
}}"""


def _analyze_arch_company(client: anthropic.Anthropic, company: ArchCompany) -> tuple[int, str]:
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


def analyze_arch_companies_ai(session: Session) -> int:
    """Generate ai_reliability_score and ai_summary for arch companies missing them."""
    api_key = get_anthropic_api_key()
    if not api_key:
        print("[ArchCompanies] Skipping AI analysis: ANTHROPIC_API_KEY is not set.")
        return 0

    client = anthropic.Anthropic(api_key=api_key)
    limit = _batch_limit("ARCH_COMPANY_AI_MAX_PER_RUN", DEFAULT_AI_BATCH_LIMIT)
    companies = session.scalars(
        select(ArchCompany)
        .where(ArchCompany.ai_reliability_score.is_(None))
        .order_by(ArchCompany.total_value.desc())
        .limit(limit)
    ).all()

    analyzed = 0
    for index, company in enumerate(companies, start=1):
        print(f"[ArchCompanies] AI {index}/{len(companies)}: {company.name[:70]}")
        try:
            score, summary = _analyze_arch_company(client, company)
            company.ai_reliability_score = score
            company.ai_summary = summary
            session.commit()
            analyzed += 1
        except Exception as exc:
            session.rollback()
            print(f"[ArchCompanies] AI analysis failed: {exc}")

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"[ArchCompanies] AI analysis complete: {analyzed} architecture firms")
    return analyzed


# ---------------------------------------------------------------------------
# Step 5 support: AI tender match (used by the API, arch_tenders only)
# ---------------------------------------------------------------------------


def match_arch_company_to_tender(company: ArchCompany, tender: ArchTender) -> dict[str, Any]:
    """Score how well an architecture firm's permit track record fits an
    arch_tenders opportunity. Returns {"match_score": int 0-100, "analysis": str}."""
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Assess how well this Vancouver architecture firm matches the tender below.

COMPANY:
{_arch_company_profile_lines(company)}

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


def run_arch_company_intelligence(session: Session) -> dict[str, int]:
    populated = populate_arch_companies_from_permits(session)
    google_enriched = enrich_arch_companies_google(session)
    ai_analyzed = analyze_arch_companies_ai(session)
    return {
        "arch_companies_populated": populated,
        "arch_companies_google_enriched": google_enriched,
        "arch_companies_ai_analyzed": ai_analyzed,
    }
