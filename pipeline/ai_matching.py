from __future__ import annotations

import json
import time
from typing import Any

import anthropic
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key, get_env
from db.models import ArchCompany, ArchTender, TenderMatch
from pipeline.arch_company_intelligence import _arch_company_profile_lines
from pipeline.company_intelligence import _extract_json, _tender_lines

MATCHING_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_COMPANIES = 10
DEFAULT_MAX_TENDERS = 50
DEFAULT_DELAY_SECONDS = 1.0


def _batch_limit(env_name: str, default: int) -> int:
    raw = get_env(env_name, str(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _request_delay() -> float:
    raw = get_env("AI_MATCHING_DELAY_SECONDS", str(DEFAULT_DELAY_SECONDS))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_DELAY_SECONDS


def _tender_catalog_json(tenders: list[ArchTender]) -> str:
    payload = [
        {
            "id": tender.id,
            "title": tender.title,
            "category": tender.category,
            "deadline": tender.deadline,
            "organization": tender.company,
            "value": tender.value,
            "status": tender.status,
        }
        for tender in tenders
    ]
    return json.dumps(payload, indent=2)


def _call_claude(client: anthropic.Anthropic, prompt: str, *, max_tokens: int = 800) -> dict[str, Any]:
    response = client.messages.create(
        model=MATCHING_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude returned no text content")
    return _extract_json(text_blocks[0])


def run_tender_matcher(
    client: anthropic.Anthropic,
    company: ArchCompany,
    tenders: list[ArchTender],
) -> list[dict[str, Any]]:
    """Agent 1: identify relevant tenders for an architecture firm."""
    if not tenders:
        return []

    prompt = f"""You are the Tender Matcher agent for TenderScope BC architecture intelligence.

Identify which tenders from the catalog are relevant for this firm based on:
- Specializations and project types (website, Houzz, permit history)
- Past project experience and notable work
- Geographic service areas vs tender organization/location
- Category alignment (architecture, engineering, design, municipal, etc.)

ARCHITECTURE FIRM:
{_arch_company_profile_lines(company)}

TENDER CATALOG (JSON):
{_tender_catalog_json(tenders)}

Return JSON only:
{{
  "matches": [
    {{
      "tender_id": <integer id from catalog>,
      "match_reason": "<one sentence why this tender is relevant>"
    }}
  ]
}}

Rules:
- Only include tenders with genuine fit (do not match everything).
- tender_id must exist in the catalog.
- Return {{"matches": []}} if nothing is relevant."""

    payload = _call_claude(client, prompt, max_tokens=1200)
    matches = payload.get("matches") or []
    if not isinstance(matches, list):
        return []

    valid_ids = {tender.id for tender in tenders}
    normalized: list[dict[str, Any]] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        try:
            tender_id = int(item.get("tender_id"))
        except (TypeError, ValueError):
            continue
        if tender_id not in valid_ids:
            continue
        reason = str(item.get("match_reason", "")).strip()
        if not reason:
            reason = "Relevant based on firm specializations and project history."
        normalized.append({"tender_id": tender_id, "match_reason": reason})
    return normalized


def run_company_scorer(
    client: anthropic.Anthropic,
    company: ArchCompany,
    tender: ArchTender,
    match_reason: str,
) -> dict[str, Any]:
    """Agent 2: score a firm-tender pair 0-100 with reasoning."""
    prompt = f"""You are the Company Scorer agent for TenderScope.

Score how well this architecture firm fits this tender on a 0-100 scale.

ARCHITECTURE FIRM:
{_arch_company_profile_lines(company)}

TENDER:
{_tender_lines(tender)}

TENDER MATCHER SIGNAL:
{match_reason}

Return JSON only:
{{
  "score": <integer 0-100>,
  "reasoning": "<2-3 sentences explaining the score based on specialization, past projects, and location fit>"
}}

Scoring guide:
- 80-100: Strong fit — specialization, portfolio, and geography align well
- 50-79: Moderate fit — some relevant experience but gaps exist
- 20-49: Weak fit — limited alignment
- 0-19: Poor fit — should not pursue"""

    payload = _call_claude(client, prompt, max_tokens=500)
    score = max(0, min(100, int(payload.get("score", 0))))
    reasoning = str(payload.get("reasoning", "")).strip()
    if not reasoning:
        reasoning = match_reason
    return {"score": score, "reasoning": reasoning}


def _arch_tender_dict(tender: ArchTender) -> dict[str, Any]:
    return {
        "id": tender.id,
        "title": tender.title,
        "company": tender.company,
        "value": tender.value,
        "deadline": tender.deadline,
        "status": tender.status,
        "category": tender.category,
        "url": tender.url,
        "tender_id": tender.tender_id,
        "ai_budget_estimate": tender.ai_budget_estimate or "",
    }


def _match_result_dict(
    tender: ArchTender,
    *,
    score: int,
    reasoning: str,
    match_reason: str,
) -> dict[str, Any]:
    return {
        "tender_id": tender.id,
        "score": score,
        "reasoning": reasoning,
        "match_reason": match_reason,
        "tender": _arch_tender_dict(tender),
    }


def _upsert_tender_match(
    session: Session,
    *,
    company_id: int,
    tender_id: int,
    score: int,
    reasoning: str,
) -> None:
    table = TenderMatch.__table__
    stmt = insert(table).values(
        company_id=company_id,
        tender_id=tender_id,
        score=score,
        reasoning=reasoning[:4000],
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["company_id", "tender_id"],
        set_={
            "score": stmt.excluded.score,
            "reasoning": stmt.excluded.reasoning,
            "created_at": func.now(),
        },
    )
    session.execute(stmt)


def _score_company_tender_matches(
    client: anthropic.Anthropic,
    session: Session,
    company: ArchCompany,
    tenders: list[ArchTender],
    tender_by_id: dict[int, ArchTender],
    *,
    persist: bool,
    min_score: int,
    delay: float,
) -> tuple[list[dict[str, Any]], int, int]:
    """Run matcher + scorer for one firm. Returns (results, candidates_found, scored_count)."""
    try:
        candidates = run_tender_matcher(client, company, tenders)
    except Exception as exc:
        print(f"[AI Matching] Matcher failed for {company.name[:50]}: {exc}")
        return [], 0, 0

    results: list[dict[str, Any]] = []
    scored_count = 0

    for candidate in candidates:
        tender = tender_by_id.get(candidate["tender_id"])
        if tender is None:
            continue

        if delay > 0:
            time.sleep(delay)

        try:
            scored = run_company_scorer(
                client,
                company,
                tender,
                candidate["match_reason"],
            )
        except Exception as exc:
            print(
                f"[AI Matching] Scorer failed for company={company.id} "
                f"tender={tender.id}: {exc}"
            )
            continue

        scored_count += 1
        if scored["score"] < min_score:
            continue

        if persist:
            _upsert_tender_match(
                session,
                company_id=company.id,
                tender_id=tender.id,
                score=scored["score"],
                reasoning=scored["reasoning"],
            )
            session.commit()

        results.append(
            _match_result_dict(
                tender,
                score=scored["score"],
                reasoning=scored["reasoning"],
                match_reason=candidate["match_reason"],
            )
        )

    return results, len(candidates), scored_count


def run_ai_matching_sync(
    session: Session,
    *,
    company_id: int,
    max_tenders: int | None = None,
    min_score: int = 65,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Run matcher + scorer for one architecture firm and return ranked matches."""
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    tender_limit = max_tenders or _batch_limit("AI_MATCHING_MAX_TENDERS", DEFAULT_MAX_TENDERS)
    delay = _request_delay()

    tenders = list(
        session.scalars(
            select(ArchTender).order_by(ArchTender.id.desc()).limit(tender_limit)
        ).all()
    )
    if not tenders:
        return []

    tender_by_id = {tender.id: tender for tender in tenders}
    company = session.scalars(
        select(ArchCompany).where(ArchCompany.id == company_id)
    ).first()
    if company is None:
        raise ValueError(f"Architecture company {company_id} not found")

    client = anthropic.Anthropic(api_key=api_key)
    print(
        f"[AI Matching] Sync run for {company.name[:70]} against {len(tenders)} tenders "
        f"(model={MATCHING_MODEL})"
    )

    results, candidates_found, scored_count = _score_company_tender_matches(
        client,
        session,
        company,
        tenders,
        tender_by_id,
        persist=True,
        min_score=min_score,
        delay=delay,
    )

    results.sort(key=lambda item: item["score"], reverse=True)
    print(
        f"[AI Matching] Sync complete: {candidates_found} candidates, "
        f"{scored_count} scored, {len(results)} above min_score={min_score}"
    )
    return results[:limit]


def run_ai_matching(
    session: Session,
    *,
    company_id: int | None = None,
    max_companies: int | None = None,
    max_tenders: int | None = None,
) -> dict[str, int]:
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    company_limit = max_companies or _batch_limit("AI_MATCHING_MAX_COMPANIES", DEFAULT_MAX_COMPANIES)
    tender_limit = max_tenders or _batch_limit("AI_MATCHING_MAX_TENDERS", DEFAULT_MAX_TENDERS)
    delay = _request_delay()

    tenders = list(
        session.scalars(
            select(ArchTender).order_by(ArchTender.id.desc()).limit(tender_limit)
        ).all()
    )
    if not tenders:
        print("[AI Matching] No architecture tenders found")
        return {"companies_processed": 0, "matches_found": 0, "matches_scored": 0}

    tender_by_id = {tender.id: tender for tender in tenders}

    company_query = select(ArchCompany).order_by(ArchCompany.total_value.desc())
    if company_id is not None:
        company_query = company_query.where(ArchCompany.id == company_id)
    companies = list(session.scalars(company_query.limit(company_limit)).all())

    if company_id is not None and not companies:
        raise ValueError(f"Architecture company {company_id} not found")

    client = anthropic.Anthropic(api_key=api_key)
    matches_found = 0
    matches_scored = 0

    print(
        f"[AI Matching] Running matcher + scorer for {len(companies)} firms "
        f"against {len(tenders)} tenders (model={MATCHING_MODEL})"
    )

    for index, company in enumerate(companies, start=1):
        print(f"[AI Matching] Company {index}/{len(companies)}: {company.name[:70]}")
        results, candidates_found, company_scored = _score_company_tender_matches(
            client,
            session,
            company,
            tenders,
            tender_by_id,
            persist=True,
            min_score=0,
            delay=delay,
        )
        matches_found += candidates_found
        matches_scored += company_scored

        if delay > 0 and index < len(companies):
            time.sleep(delay)

    print(
        f"[AI Matching] Complete: {matches_found} candidates, {matches_scored} scored matches stored"
    )
    return {
        "companies_processed": len(companies),
        "matches_found": matches_found,
        "matches_scored": matches_scored,
    }
