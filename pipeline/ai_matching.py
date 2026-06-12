from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import anthropic
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key, get_env
from db.models import (
    ArchCompany,
    ArchTender,
    CommercialTender,
    Company,
    Tender,
    TenderMatch,
)
from pipeline.arch_company_intelligence import _arch_company_profile_lines
from pipeline.company_intelligence import _company_profile_lines, _extract_json, _tender_lines

ConstructionTender = Tender | CommercialTender
CatalogEntry = tuple[str, ArchTender | ConstructionTender]

MATCHING_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_COMPANIES = 10
DEFAULT_MAX_TENDERS = 50
DEFAULT_DELAY_SECONDS = 1.0

# Hybrid discover: rule top-N → Haiku scorer-only, weekly cache refresh
TENDER_MATCH_CACHE_MAX_AGE_HOURS = 168
HYBRID_AI_CANDIDATE_LIMIT = 20
HYBRID_INLINE_SCORE_CAP = 5

CompanyKind = Literal["construction", "architecture"]


@dataclass(frozen=True)
class TenderPairCandidate:
    tender_source: str
    tender_id: int
    match_reason: str


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


_BREAKDOWN_KEYS = (
    "keywords",
    "category",
    "specialization",
    "location",
    "value",
    "reliability",
    "freshness",
)

_SCORER_JSON_SCHEMA = """
Return JSON only:
{
  "score": <integer 0-100 — MUST equal the sum of all breakdown.points>,
  "reasoning": "<2-3 sentences explaining the overall fit>",
  "breakdown": {
    "keywords": { "points": <0-35>, "detail": "<keyword / trade / specialty overlap>" },
    "category": { "points": <0-20>, "detail": "<project type or category alignment>" },
    "specialization": { "points": <0-15>, "detail": "<domain expertise fit>" },
    "location": { "points": <0-15>, "detail": "<geography or service area fit>" },
    "value": { "points": <0-15>, "detail": "<project scale / budget fit>" },
    "reliability": { "points": <0-5>, "detail": "<track record / reputation signal>" },
    "freshness": { "points": <0-10>, "detail": "<deadline urgency / timing>" }
  }
}

Rules:
- Assign 0 points with a brief detail when a factor does not apply.
- The score field MUST equal the sum of all breakdown.points (max 100).
"""


def _normalize_scorer_payload(payload: dict[str, Any], match_reason: str) -> dict[str, Any]:
    breakdown_raw = payload.get("breakdown")
    breakdown: dict[str, dict[str, Any]] = {}
    for key in _BREAKDOWN_KEYS:
        item = breakdown_raw.get(key) if isinstance(breakdown_raw, dict) else None
        if not isinstance(item, dict):
            item = {}
        try:
            points = int(item.get("points", 0))
        except (TypeError, ValueError):
            points = 0
        points = max(0, points)
        detail = str(item.get("detail", "")).strip() or "No significant signal"
        breakdown[key] = {"points": points, "detail": detail}

    computed = sum(item["points"] for item in breakdown.values())
    try:
        declared = int(payload.get("score", computed))
    except (TypeError, ValueError):
        declared = computed
    score = max(0, min(100, computed if computed > 0 else declared))
    reasoning = str(payload.get("reasoning", "")).strip() or match_reason
    return {"score": score, "reasoning": reasoning, "breakdown": breakdown}


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
        normalized.append(
            {"tender_id": tender_id, "tender_source": "arch", "match_reason": reason}
        )
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

{_SCORER_JSON_SCHEMA}

Scoring guide:
- 80-100: Strong fit — specialization, portfolio, and geography align well
- 50-79: Moderate fit — some relevant experience but gaps exist
- 20-49: Weak fit — limited alignment
- 0-19: Poor fit — should not pursue"""

    payload = _call_claude(client, prompt, max_tokens=900)
    return _normalize_scorer_payload(payload, match_reason)


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
    *,
    tender_id: int,
    tender_source: str,
    tender_payload: dict[str, Any],
    score: int,
    reasoning: str,
    match_reason: str,
    breakdown: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "tender_id": tender_id,
        "tender_source": tender_source,
        "score": score,
        "reasoning": reasoning,
        "match_reason": match_reason,
        "tender": tender_payload,
    }
    if breakdown is not None:
        result["breakdown"] = breakdown
    return result


def build_match_reason_from_rules(reasons: list[str]) -> str:
    if not reasons:
        return "Rule-based tender match candidate."
    return "; ".join(reasons[:3])[:500]


def _cache_created_at_utc(created_at: datetime | None) -> datetime | None:
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc)


def is_tender_match_cache_fresh(
    created_at: datetime | None,
    *,
    max_age_hours: int = TENDER_MATCH_CACHE_MAX_AGE_HOURS,
) -> bool:
    stamped = _cache_created_at_utc(created_at)
    if stamped is None:
        return False
    return datetime.now(timezone.utc) - stamped <= timedelta(hours=max_age_hours)


def get_fresh_cached_match(
    session: Session,
    *,
    company_kind: CompanyKind,
    company_id: int,
    tender_source: str,
    tender_id: int,
    max_age_hours: int = TENDER_MATCH_CACHE_MAX_AGE_HOURS,
) -> TenderMatch | None:
    row = session.scalars(
        select(TenderMatch).where(
            TenderMatch.company_kind == company_kind,
            TenderMatch.company_id == company_id,
            TenderMatch.tender_source == tender_source,
            TenderMatch.tender_id == tender_id,
        )
    ).first()
    if row is None or not is_tender_match_cache_fresh(row.created_at, max_age_hours=max_age_hours):
        return None
    return row


def _load_tender_row(
    session: Session,
    tender_source: str,
    tender_id: int,
) -> ArchTender | ConstructionTender | None:
    if tender_source == "arch":
        return session.get(ArchTender, tender_id)
    if tender_source == "federal":
        return session.get(Tender, tender_id)
    if tender_source == "commercial":
        return session.get(CommercialTender, tender_id)
    return None


def score_tender_pairs(
    session: Session,
    company: Company | ArchCompany,
    kind: CompanyKind,
    candidates: list[TenderPairCandidate],
    *,
    persist: bool = True,
    max_age_hours: int = TENDER_MATCH_CACHE_MAX_AGE_HOURS,
    inline_cap: int | None = None,
) -> dict[str, Any]:
    """Scorer-only hybrid path: skip Haiku when cache is fresh; cap new API calls per run."""
    pairs: dict[tuple[str, int], dict[str, Any]] = {}
    stats = {
        "cache_hits": 0,
        "freshly_scored": 0,
        "skipped_cap": 0,
        "skipped_no_key": 0,
        "api_errors": 0,
        "api_key_missing": False,
    }
    api_calls = 0

    company_id = company.id
    api_key = get_anthropic_api_key()
    client: anthropic.Anthropic | None = None
    delay = _request_delay()

    for candidate in candidates:
        key = (candidate.tender_source, candidate.tender_id)
        cached = get_fresh_cached_match(
            session,
            company_kind=kind,
            company_id=company_id,
            tender_source=candidate.tender_source,
            tender_id=candidate.tender_id,
            max_age_hours=max_age_hours,
        )
        if cached is not None:
            stats["cache_hits"] += 1
            pairs[key] = {
                "score": cached.score,
                "reasoning": (cached.reasoning or "").strip(),
                "origin": "cache",
            }
            continue

        if inline_cap is not None and api_calls >= inline_cap:
            stats["skipped_cap"] += 1
            continue

        if client is None:
            if not api_key:
                stats["api_key_missing"] = True
                continue
            client = anthropic.Anthropic(api_key=api_key)

        tender = _load_tender_row(session, candidate.tender_source, candidate.tender_id)
        if tender is None:
            stats["skipped_no_key"] += 1
            continue

        if api_calls > 0 and delay > 0:
            time.sleep(delay)

        try:
            if kind == "construction" and isinstance(company, Company):
                scored = run_construction_company_scorer(
                    client,
                    company,
                    tender,
                    candidate.tender_source,
                    candidate.match_reason,
                )
            elif kind == "architecture" and isinstance(company, ArchCompany):
                scored = run_company_scorer(
                    client,
                    company,
                    tender,
                    candidate.match_reason,
                )
            else:
                stats["skipped_no_key"] += 1
                continue
        except Exception as exc:
            stats["api_errors"] += 1
            print(
                f"[AI Matching] Hybrid scorer failed for company={company_id} "
                f"tender={candidate.tender_source}:{candidate.tender_id}: {exc}"
            )
            continue

        api_calls += 1
        stats["freshly_scored"] += 1
        reasoning = scored["reasoning"]
        score = int(scored["score"])

        if persist:
            _upsert_tender_match(
                session,
                company_kind=kind,
                company_id=company_id,
                tender_source=candidate.tender_source,
                tender_id=candidate.tender_id,
                score=score,
                reasoning=reasoning,
            )
            session.commit()

        pairs[key] = {
            "score": score,
            "reasoning": reasoning,
            "origin": "fresh",
        }

    return {"pairs": pairs, **stats}


def load_fresh_company_tender_matches(
    session: Session,
    *,
    company_kind: CompanyKind,
    company_id: int,
    max_age_hours: int = TENDER_MATCH_CACHE_MAX_AGE_HOURS,
) -> list[TenderMatch]:
    """All tender_matches rows for a company that are still within the cache TTL."""
    rows = session.scalars(
        select(TenderMatch)
        .where(
            TenderMatch.company_kind == company_kind,
            TenderMatch.company_id == company_id,
        )
        .order_by(TenderMatch.score.desc(), TenderMatch.id.desc())
    ).all()
    return [
        row
        for row in rows
        if is_tender_match_cache_fresh(row.created_at, max_age_hours=max_age_hours)
    ]


def resolve_hybrid_tender_score(
    session: Session,
    *,
    company_kind: CompanyKind,
    company_id: int,
    tender_source: str,
    tender_id: int,
    rule_score: int,
    rule_reasons: list[str],
    max_age_hours: int = TENDER_MATCH_CACHE_MAX_AGE_HOURS,
    hybrid_pairs: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> tuple[int, list[str], str]:
    """Use in-run hybrid pairs, then fresh AI cache; otherwise fall back to rule score."""
    key = (tender_source, tender_id)
    if hybrid_pairs and key in hybrid_pairs:
        pair = hybrid_pairs[key]
        reasoning = (pair.get("reasoning") or "").strip()
        reasons = [reasoning[:240]] if reasoning else list(rule_reasons)
        return int(pair["score"]), reasons, "ai_match"

    cached = get_fresh_cached_match(
        session,
        company_kind=company_kind,
        company_id=company_id,
        tender_source=tender_source,
        tender_id=tender_id,
        max_age_hours=max_age_hours,
    )
    if cached is not None:
        reasoning = (cached.reasoning or "").strip()
        reasons = [reasoning[:240]] if reasoning else list(rule_reasons)
        return cached.score, reasons, "ai_match"
    return rule_score, list(rule_reasons), "rules"


def warm_hybrid_tender_cache(
    session: Session,
    *,
    company_id: int,
    kind: CompanyKind,
    candidates: list[TenderPairCandidate],
    inline_cap: int | None = None,
) -> dict[str, Any]:
    """Score rule-selected pairs and persist; used by Discover (capped) and warm-cache script."""
    if kind == "construction":
        company = session.get(Company, company_id)
        if company is None:
            raise ValueError(f"Company {company_id} not found")
    else:
        company = session.get(ArchCompany, company_id)
        if company is None:
            raise ValueError(f"Architecture company {company_id} not found")

    return score_tender_pairs(
        session,
        company,
        kind,
        candidates,
        persist=True,
        inline_cap=inline_cap,
    )


def _upsert_tender_match(
    session: Session,
    *,
    company_kind: str,
    company_id: int,
    tender_source: str,
    tender_id: int,
    score: int,
    reasoning: str,
) -> None:
    table = TenderMatch.__table__
    stmt = insert(table).values(
        company_kind=company_kind,
        company_id=company_id,
        tender_source=tender_source,
        tender_id=tender_id,
        score=score,
        reasoning=reasoning[:4000],
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["company_kind", "company_id", "tender_source", "tender_id"],
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
                company_kind="architecture",
                company_id=company.id,
                tender_source="arch",
                tender_id=tender.id,
                score=scored["score"],
                reasoning=scored["reasoning"],
            )
            session.commit()

        payload = _arch_tender_dict(tender)
        payload["tender_source"] = "arch"
        results.append(
            _match_result_dict(
                tender_id=tender.id,
                tender_source="arch",
                tender_payload=payload,
                score=scored["score"],
                reasoning=scored["reasoning"],
                match_reason=candidate["match_reason"],
                breakdown=scored.get("breakdown"),
            )
        )

    return results, len(candidates), scored_count


def _construction_tender_catalog_json(entries: list[CatalogEntry]) -> str:
    payload = []
    for source, tender in entries:
        if source == "federal":
            payload.append(
                {
                    "id": tender.id,
                    "source": source,
                    "title": tender.title,
                    "category": tender.category,
                    "deadline": getattr(tender, "closing_date", "") or "",
                    "organization": getattr(tender, "organization", "") or "",
                    "value": getattr(tender, "estimated_value", "") or "",
                    "location": getattr(tender, "location", "") or "",
                }
            )
        else:
            payload.append(
                {
                    "id": tender.id,
                    "source": source,
                    "title": tender.title,
                    "category": tender.category,
                    "deadline": getattr(tender, "deadline", "") or "",
                    "organization": getattr(tender, "company", "") or "",
                    "value": getattr(tender, "value", "") or "",
                }
            )
    return json.dumps(payload, indent=2)


def _construction_tender_dict(source: str, tender: ConstructionTender) -> dict[str, Any]:
    if source == "federal":
        return {
            "id": tender.id,
            "title": tender.title,
            "company": getattr(tender, "organization", "") or "",
            "value": getattr(tender, "estimated_value", "") or "",
            "deadline": getattr(tender, "closing_date", "") or "",
            "category": tender.category or "",
            "location": getattr(tender, "location", "") or "",
            "url": getattr(tender, "url", "") or "",
            "tender_id": getattr(tender, "tender_id", "") or "",
            "ai_budget_estimate": getattr(tender, "ai_budget_estimate", "") or "",
            "tender_source": "federal",
        }
    return {
        "id": tender.id,
        "title": tender.title,
        "company": getattr(tender, "company", "") or "",
        "value": getattr(tender, "value", "") or "",
        "deadline": getattr(tender, "deadline", "") or "",
        "status": getattr(tender, "status", "") or "",
        "category": tender.category or "Commercial",
        "url": getattr(tender, "url", "") or "",
        "tender_id": getattr(tender, "tender_id", "") or "",
        "ai_budget_estimate": getattr(tender, "ai_budget_estimate", "") or "",
        "tender_source": "commercial",
    }


def _load_construction_tender_catalog(
    session: Session,
    max_tenders: int,
) -> list[CatalogEntry]:
    per_source = max(1, max_tenders // 2)
    federal = list(
        session.scalars(
            select(Tender).order_by(Tender.id.desc()).limit(per_source)
        ).all()
    )
    commercial = list(
        session.scalars(
            select(CommercialTender).order_by(CommercialTender.id.desc()).limit(per_source)
        ).all()
    )
    return [("federal", row) for row in federal] + [("commercial", row) for row in commercial]


def run_construction_tender_matcher(
    client: anthropic.Anthropic,
    company: Company,
    catalog: list[CatalogEntry],
) -> list[dict[str, Any]]:
    """Agent 1: identify relevant tenders for a construction company."""
    if not catalog:
        return []

    prompt = f"""You are the Tender Matcher agent for TenderScope BC construction intelligence.

Identify which tenders from the catalog are relevant for this company based on:
- Vancouver building permit history (project types, volume, value range)
- Trade and construction specialties implied by permit types
- Geographic areas of activity vs tender location/organization
- Project scale fit (typical job size vs tender value)

CONSTRUCTION COMPANY:
{_company_profile_lines(company)}

TENDER CATALOG (JSON):
{_construction_tender_catalog_json(catalog)}

Return JSON only:
{{
  "matches": [
    {{
      "tender_id": <integer id from catalog>,
      "tender_source": "<federal or commercial — must match catalog source>",
      "match_reason": "<one sentence why this tender is relevant>"
    }}
  ]
}}

Rules:
- Only include tenders with genuine fit (do not match everything).
- tender_id and tender_source must match an entry in the catalog.
- Return {{"matches": []}} if nothing is relevant."""

    payload = _call_claude(client, prompt, max_tokens=1200)
    matches = payload.get("matches") or []
    if not isinstance(matches, list):
        return []

    valid_keys = {(source, tender.id) for source, tender in catalog}
    normalized: list[dict[str, Any]] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        try:
            tender_id = int(item.get("tender_id"))
        except (TypeError, ValueError):
            continue
        tender_source = str(item.get("tender_source", "")).strip().lower()
        if tender_source not in {"federal", "commercial"}:
            continue
        if (tender_source, tender_id) not in valid_keys:
            continue
        reason = str(item.get("match_reason", "")).strip()
        if not reason:
            reason = "Relevant based on permit history and project types."
        normalized.append(
            {
                "tender_id": tender_id,
                "tender_source": tender_source,
                "match_reason": reason,
            }
        )
    return normalized


def run_construction_company_scorer(
    client: anthropic.Anthropic,
    company: Company,
    tender: ConstructionTender,
    tender_source: str,
    match_reason: str,
) -> dict[str, Any]:
    """Agent 2: score a construction company-tender pair 0-100 with reasoning."""
    prompt = f"""You are the Company Scorer agent for TenderScope BC construction intelligence.

Score how well this construction company fits this tender on a 0-100 scale.

CONSTRUCTION COMPANY:
{_company_profile_lines(company)}

TENDER:
{_tender_lines(tender)}

TENDER MATCHER SIGNAL:
{match_reason}

{_SCORER_JSON_SCHEMA}

Scoring guide:
- 80-100: Strong fit — trade, scale, and geography align well
- 50-79: Moderate fit — some relevant experience but gaps exist
- 20-49: Weak fit — limited alignment
- 0-19: Poor fit — should not pursue"""

    payload = _call_claude(client, prompt, max_tokens=900)
    return _normalize_scorer_payload(payload, match_reason)


def _score_construction_tender_matches(
    client: anthropic.Anthropic,
    session: Session,
    company: Company,
    catalog: list[CatalogEntry],
    catalog_by_key: dict[tuple[str, int], ConstructionTender],
    *,
    persist: bool,
    min_score: int,
    delay: float,
) -> tuple[list[dict[str, Any]], int, int]:
    try:
        candidates = run_construction_tender_matcher(client, company, catalog)
    except Exception as exc:
        print(f"[AI Matching] Construction matcher failed for {company.name[:50]}: {exc}")
        return [], 0, 0

    results: list[dict[str, Any]] = []
    scored_count = 0

    for candidate in candidates:
        key = (candidate["tender_source"], candidate["tender_id"])
        tender = catalog_by_key.get(key)
        if tender is None:
            continue

        if delay > 0:
            time.sleep(delay)

        try:
            scored = run_construction_company_scorer(
                client,
                company,
                tender,
                candidate["tender_source"],
                candidate["match_reason"],
            )
        except Exception as exc:
            print(
                f"[AI Matching] Construction scorer failed for company={company.id} "
                f"tender={candidate['tender_source']}:{candidate['tender_id']}: {exc}"
            )
            continue

        scored_count += 1
        if scored["score"] < min_score:
            continue

        tender_source = candidate["tender_source"]
        if persist:
            _upsert_tender_match(
                session,
                company_kind="construction",
                company_id=company.id,
                tender_source=tender_source,
                tender_id=tender.id,
                score=scored["score"],
                reasoning=scored["reasoning"],
            )
            session.commit()

        payload = _construction_tender_dict(tender_source, tender)
        results.append(
            _match_result_dict(
                tender_id=tender.id,
                tender_source=tender_source,
                tender_payload=payload,
                score=scored["score"],
                reasoning=scored["reasoning"],
                match_reason=candidate["match_reason"],
                breakdown=scored.get("breakdown"),
            )
        )

    return results, len(candidates), scored_count


def run_construction_ai_matching_sync(
    session: Session,
    *,
    company_id: int,
    max_tenders: int | None = None,
    min_score: int = 65,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Run matcher + scorer for one construction company and return ranked matches."""
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    tender_limit = max_tenders or _batch_limit("AI_MATCHING_MAX_TENDERS", DEFAULT_MAX_TENDERS)
    delay = _request_delay()

    catalog = _load_construction_tender_catalog(session, tender_limit)
    if not catalog:
        return []

    catalog_by_key: dict[tuple[str, int], ConstructionTender] = {
        (source, tender.id): tender for source, tender in catalog
    }
    company = session.scalars(
        select(Company).where(Company.id == company_id)
    ).first()
    if company is None:
        raise ValueError(f"Construction company {company_id} not found")

    client = anthropic.Anthropic(api_key=api_key)
    print(
        f"[AI Matching] Construction sync run for {company.name[:70]} against "
        f"{len(catalog)} tenders (model={MATCHING_MODEL})"
    )

    results, candidates_found, scored_count = _score_construction_tender_matches(
        client,
        session,
        company,
        catalog,
        catalog_by_key,
        persist=True,
        min_score=min_score,
        delay=delay,
    )

    results.sort(key=lambda item: item["score"], reverse=True)
    print(
        f"[AI Matching] Construction sync complete: {candidates_found} candidates, "
        f"{scored_count} scored, {len(results)} above min_score={min_score}"
    )
    return results[:limit]


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


def run_unified_ai_matching_sync(
    session: Session,
    *,
    company_id: int,
    kind: str,
    max_tenders: int | None = None,
    min_score: int = 65,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Single entry point for sync AI matching — construction and architecture."""
    normalized = (kind or "architecture").strip().lower()
    if normalized == "construction":
        return run_construction_ai_matching_sync(
            session,
            company_id=company_id,
            max_tenders=max_tenders,
            min_score=min_score,
            limit=limit,
        )
    if normalized == "architecture":
        return run_ai_matching_sync(
            session,
            company_id=company_id,
            max_tenders=max_tenders,
            min_score=min_score,
            limit=limit,
        )
    raise ValueError("kind must be 'architecture' or 'construction'")


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
