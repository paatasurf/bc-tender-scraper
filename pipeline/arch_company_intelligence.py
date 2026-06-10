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
    MatchableTender,
    _batch_limit,
    _extract_json,
    _fetch_google_place,
    _google_api_key,
    _neighborhood_from_address,
    _parse_value,
    _tender_lines,
)

EXCLUDED_ARCHITECT_VALUES = {"", "N/A"}

ARCH_AI_REQUEST_DELAY_SECONDS = 3.0
RATE_LIMIT_BACKOFF_SECONDS = (30, 60, 120)


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
    lines = [f"Company: {company.name}", f"Google rating: {rating}"]
    if company.website:
        lines.append(f"Website: {company.website}")
    if company.houzz_profile_url:
        houzz = (
            f"{company.houzz_rating}/5 ({company.houzz_reviews_count} reviews, "
            f"{company.houzz_projects_count or 0} projects)"
            if company.houzz_rating is not None
            else "Profile found, no rating yet"
        )
        lines.append(f"Houzz: {houzz}")
        if company.houzz_project_types:
            lines.append(f"Houzz project types: {', '.join(company.houzz_project_types)}")
    if company.website_projects_count is not None:
        lines.append(
            f"Website portfolio: {company.website_projects_count} BC projects showcased"
        )
        if company.website_specializations:
            lines.append(f"Specializations: {', '.join(company.website_specializations)}")
        if company.website_service_areas:
            lines.append(f"BC service areas: {', '.join(company.website_service_areas[:8])}")
        if company.website_notable_projects:
            lines.append(f"Notable BC projects: {', '.join(company.website_notable_projects[:5])}")
    if company.total_projects:
        lines.extend(
            [
                f"Permit history (bonus only): {company.total_projects} permits, "
                f"${company.total_value:,.0f} CAD total",
                f"Project types: {', '.join(company.project_types or []) or 'Unknown'}",
                f"Areas of activity: {', '.join((company.neighborhoods or [])[:8]) or 'Unknown'}",
            ]
        )
    else:
        lines.append(
            "Permit history: none on record (normal for architecture firms — not scored negatively)"
        )
    return "\n".join(lines)


def _score_google_component(company: ArchCompany) -> float:
    """Google reputation — 50% of reliability score."""
    max_pts = 50.0
    if company.google_rating is not None:
        return (company.google_rating / 5.0) * max_pts
    reviews = company.google_reviews_count or 0
    if reviews > 0:
        return min(max_pts * 0.5, (reviews / 50.0) * max_pts * 0.5)
    return max_pts * 0.3  # neutral when not yet enriched — not a permit penalty


def _score_houzz_component(company: ArchCompany) -> float:
    """Houzz profile quality — 30% of reliability score."""
    max_pts = 30.0
    if company.houzz_rating is not None:
        rating_pts = (company.houzz_rating / 5.0) * max_pts * 0.7
        review_pts = min(max_pts * 0.3, ((company.houzz_reviews_count or 0) / 100.0) * max_pts * 0.3)
        return min(max_pts, rating_pts + review_pts)
    if (company.houzz_reviews_count or 0) > 0:
        return min(max_pts * 0.4, (company.houzz_reviews_count / 50.0) * max_pts * 0.4)
    if company.houzz_profile_url:
        return max_pts * 0.2
    return max_pts * 0.25  # neutral when no Houzz profile


def _score_website_component(company: ArchCompany) -> float:
    """Website research depth — 20% of reliability score."""
    max_pts = 20.0
    if company.website_projects_count is None:
        return max_pts * 0.25  # not researched yet — neutral
    pts = 0.0
    if company.website_projects_count > 0:
        pts += min(10.0, (company.website_projects_count / 10.0) * 10.0)
    if company.website_specializations:
        pts += min(5.0, len(company.website_specializations) * 1.5)
    if company.website_service_areas:
        pts += min(5.0, len(company.website_service_areas) * 0.5)
    return min(max_pts, pts)


def _compute_arch_reliability_score(company: ArchCompany) -> int:
    score = round(
        _score_google_component(company)
        + _score_houzz_component(company)
        + _score_website_component(company)
    )
    score = max(0, min(100, score))
    if company.google_rating is not None and company.google_rating > 4.0:
        score = max(score, 50)
    return score


def _build_summary_prompt(company: ArchCompany) -> str:
    score = _compute_arch_reliability_score(company)
    return f"""Write a 2-sentence architecture firm profile. Score is pre-computed at {score}/100.

Firm: {company.name}
Google: {company.google_rating or "n/a"} ({company.google_reviews_count or 0} reviews)
Houzz projects: {company.houzz_projects_count or 0}
Website BC projects: {company.website_projects_count if company.website_projects_count is not None else "n/a"}

Return JSON only: {{"summary": "<2 sentences>"}}"""


def _fallback_summary(company: ArchCompany, score: int) -> str:
    parts = [
        f"{company.name} scores {score}/100 for reliability based on public Google, Houzz, and website data."
    ]
    if company.google_rating is not None:
        parts.append(
            f"Google rating is {company.google_rating}/5 from {company.google_reviews_count or 0} reviews."
        )
    if company.houzz_projects_count:
        parts.append(f"Houzz lists {company.houzz_projects_count} portfolio projects.")
    if company.website_projects_count:
        parts.append(f"The firm website showcases {company.website_projects_count} BC projects.")
    if company.total_projects:
        parts.append(f"{company.total_projects} Vancouver building permits are on record.")
    return " ".join(parts)


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return "429" in message or "rate limit" in message


def _fetch_claude_summary(client: anthropic.Anthropic, company: ArchCompany) -> str:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": _build_summary_prompt(company)}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")

    payload = _extract_json(text_blocks[0])
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise ValueError("Claude response missing summary")
    return summary


def _fetch_claude_summary_with_retry(client: anthropic.Anthropic, company: ArchCompany) -> str:
    last_error: Exception | None = None
    for attempt in range(len(RATE_LIMIT_BACKOFF_SECONDS) + 1):
        try:
            return _fetch_claude_summary(client, company)
        except Exception as exc:
            last_error = exc
            if not _is_rate_limit_error(exc) or attempt >= len(RATE_LIMIT_BACKOFF_SECONDS):
                raise
            wait = RATE_LIMIT_BACKOFF_SECONDS[attempt]
            print(
                f"[ArchCompanies] Rate limited for {company.name[:50]!r}, "
                f"waiting {wait}s (attempt {attempt + 1}/{len(RATE_LIMIT_BACKOFF_SECONDS)})..."
            )
            time.sleep(wait)
    if last_error:
        raise last_error
    raise RuntimeError("Claude summary failed without error")


def backfill_arch_reliability_scores(session: Session) -> int:
    """Set ai_reliability_score for all firms missing it — no Claude API calls."""
    companies = session.scalars(
        select(ArchCompany).where(ArchCompany.ai_reliability_score.is_(None))
    ).all()
    if not companies:
        return 0

    print(f"[ArchCompanies] Backfilling {len(companies)} reliability scores (local, no API)...")
    for company in companies:
        score = _compute_arch_reliability_score(company)
        company.ai_reliability_score = score
        if not (company.ai_summary or "").strip():
            company.ai_summary = _fallback_summary(company, score)

    session.commit()
    print(f"[ArchCompanies] Backfilled {len(companies)} reliability scores")
    return len(companies)


def _needs_claude_summary(company: ArchCompany) -> bool:
    summary = (company.ai_summary or "").strip()
    if not summary:
        return True
    return summary.startswith(f"{company.name} scores")


def _refresh_arch_reliability_scores(session: Session) -> int:
    """Recompute deterministic scores after website research enriches firm data."""
    companies = session.scalars(
        select(ArchCompany).where(ArchCompany.ai_reliability_score.isnot(None))
    ).all()
    updated = 0
    for company in companies:
        new_score = _compute_arch_reliability_score(company)
        if new_score != company.ai_reliability_score:
            company.ai_reliability_score = new_score
            updated += 1
    if updated:
        session.commit()
    return updated


def analyze_arch_companies_ai(session: Session) -> int:
    """Backfill reliability scores locally, then optionally upgrade summaries via Claude."""
    scored = backfill_arch_reliability_scores(session)

    api_key = get_anthropic_api_key()
    if not api_key:
        print("[ArchCompanies] Skipping Claude summaries: ANTHROPIC_API_KEY is not set.")
        return scored

    client = anthropic.Anthropic(api_key=api_key)
    limit = _batch_limit("ARCH_COMPANY_AI_MAX_PER_RUN", DEFAULT_AI_BATCH_LIMIT)
    companies = session.scalars(
        select(ArchCompany)
        .where(ArchCompany.ai_reliability_score.isnot(None))
        .order_by(ArchCompany.total_value.desc())
        .limit(limit)
    ).all()

    candidates = [c for c in companies if _needs_claude_summary(c)]
    print(
        f"[ArchCompanies] Claude summaries: {len(candidates)} firms need enrichment "
        f"(max {limit}, {scored} scores backfilled)"
    )

    enriched = 0
    for index, company in enumerate(candidates, start=1):
        if index > 1:
            time.sleep(ARCH_AI_REQUEST_DELAY_SECONDS)

        print(f"[ArchCompanies] AI summary {index}/{len(candidates)}: {company.name[:70]}")
        try:
            summary = _fetch_claude_summary_with_retry(client, company)
            company.ai_summary = summary
            session.commit()
            enriched += 1
        except Exception as exc:
            session.rollback()
            print(f"[ArchCompanies] Claude summary failed for {company.name[:50]}: {exc}")

    print(f"[ArchCompanies] AI analysis complete: {scored} scored, {enriched} summaries enriched")
    return scored + enriched


# ---------------------------------------------------------------------------
# Step 5 support: AI tender match (used by the API, arch_tenders only)
# ---------------------------------------------------------------------------


def match_arch_company_to_tender(company: ArchCompany, tender: MatchableTender) -> dict[str, Any]:
    """Score how well an architecture firm fits a tender and suggest a win strategy."""
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""You are a bid strategist helping a Vancouver BC architecture firm win a public tender.

COMPANY:
{_arch_company_profile_lines(company)}

TENDER:
{_tender_lines(tender)}

Assess fit and provide a win strategy. Missing permit history must NOT reduce win probability.
Focus on Google reputation, Houzz portfolio, website specializations, and BC project experience.

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
        recommendations.append("Highlight relevant BC portfolio projects in your proposal.")
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


def run_arch_company_intelligence(session: Session) -> dict[str, int]:
    from pipeline.research_arch_websites import research_arch_websites
    from pipeline.scrape_arch_aibc import scrape_arch_aibc
    from pipeline.scrape_arch_companies_google import scrape_arch_companies_google
    from pipeline.scrape_arch_houzz import scrape_arch_houzz

    populated = populate_arch_companies_from_permits(session)

    try:
        scraped = scrape_arch_companies_google(session)
    except Exception as exc:
        print(f"[ArchCompanies] Google Places scrape failed: {exc}")
        scraped = 0

    try:
        google_enriched = enrich_arch_companies_google(session)
    except Exception as exc:
        print(f"[ArchCompanies] Google enrichment failed: {exc}")
        google_enriched = 0

    # Score all firms immediately — must run before slow Houzz / website / Claude steps.
    try:
        scores_backfilled = backfill_arch_reliability_scores(session)
    except Exception as exc:
        print(f"[ArchCompanies] Score backfill failed: {exc}")
        scores_backfilled = 0

    try:
        ai_analyzed = analyze_arch_companies_ai(session)
    except Exception as exc:
        print(f"[ArchCompanies] AI analysis failed: {exc}")
        ai_analyzed = scores_backfilled

    try:
        houzz_scraped = scrape_arch_houzz(session)
    except Exception as exc:
        print(f"[ArchCompanies] Houzz scrape failed: {exc}")
        houzz_scraped = 0

    try:
        aibc_verified = scrape_arch_aibc(session)
    except Exception as exc:
        print(f"[ArchCompanies] AIBC scrape failed: {exc}")
        aibc_verified = 0

    try:
        scores_refreshed = _refresh_arch_reliability_scores(session)
        if scores_refreshed:
            print(f"[ArchCompanies] Refreshed {scores_refreshed} scores after Houzz enrichment")
    except Exception as exc:
        print(f"[ArchCompanies] Score refresh failed: {exc}")
        scores_refreshed = 0

    try:
        websites_researched = research_arch_websites(session)
    except Exception as exc:
        print(f"[ArchCompanies] Website research failed: {exc}")
        websites_researched = 0

    try:
        scores_refreshed += _refresh_arch_reliability_scores(session)
    except Exception as exc:
        print(f"[ArchCompanies] Post-website score refresh failed: {exc}")

    try:
        backfill_arch_reliability_scores(session)
    except Exception as exc:
        print(f"[ArchCompanies] Final score backfill failed: {exc}")

    return {
        "arch_companies_populated": populated,
        "arch_companies_google_scraped": scraped,
        "arch_companies_houzz_scraped": houzz_scraped,
        "arch_companies_aibc_verified": aibc_verified,
        "arch_companies_google_enriched": google_enriched,
        "arch_companies_websites_researched": websites_researched,
        "arch_companies_scores_backfilled": scores_backfilled,
        "arch_companies_ai_analyzed": ai_analyzed,
        "arch_companies_scores_refreshed": scores_refreshed,
    }
