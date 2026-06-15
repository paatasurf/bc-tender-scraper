"""Deterministic construction company ↔ tender match scoring (constitution-compliant)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from db.models import CommercialTender, Company, Tender
from pipeline.company_intelligence import _parse_value
from pipeline.scoring.explain import BreakdownFactor, build_reasons
from pipeline.scoring.match_scoring_common import (
    STOP_WORDS,
    _factor_to_json,
    _parse_date,
    _token_set,
    assert_score_equals_breakdown,
    breakdown_json_to_api_breakdown_generic,
    tokenize,
    tokenize_geo,
    to_api_breakdown_seven_key,
)

ConstructionTender = Tender | CommercialTender

MAX_KEYWORDS = 35
MAX_CATEGORY = 20
MAX_SPECIALIZATION = 15
MAX_LOCATION = 15
MAX_VALUE = 15
MAX_RELIABILITY = 5
MAX_FRESHNESS = 10

CANONICAL_KEYS = (
    "keywords",
    "category",
    "specialization",
    "location",
    "value",
    "reliability",
    "freshness",
)

KEYWORD_EXPANSIONS: dict[str, list[str]] = {
    "building": ["construction", "build", "facility", "structure"],
    "alteration": ["renovation", "retrofit", "upgrade", "repair", "restoration", "rehabilitation", "improvement"],
    "addition": ["expansion", "extension", "renovation"],
    "demolition": ["deconstruction", "removal", "abatement"],
    "salvage": ["abatement", "hazardous", "remediation"],
    "plumbing": ["mechanical", "hvac", "pipe"],
    "electrical": ["electric", "lighting", "power"],
    "concrete": ["paving", "foundation", "structural", "civil"],
    "roofing": ["roof", "envelope", "cladding"],
    "landscape": ["landscaping", "site", "civil", "exterior"],
    "code": ["consulting", "compliance", "engineering", "advisory"],
    "consultants": ["consulting", "advisory", "engineering", "professional"],
}

GENERIC_TENDER_TOKENS = frozenset(
    {
        "construction",
        "build",
        "repair",
        "upgrade",
        "design",
        "engineering",
        "interior",
        "renovation",
        "renovate",
        "consulting",
        "services",
        "professional",
        "project",
    }
)

_STREET_TOKEN_RE = re.compile(
    r"\b(street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln|way|court|ct|crescent|cres)\b",
    re.IGNORECASE,
)


def _tender_haystack(tender: ConstructionTender, tender_source: str) -> str:
    if tender_source == "federal":
        org = getattr(tender, "organization", "") or ""
        location = getattr(tender, "location", "") or ""
        return f"{tender.title} {tender.category} {org} {location}".strip()
    company = getattr(tender, "company", "") or ""
    return f"{tender.title} {tender.category} {company}".strip()


def _tender_value(tender: ConstructionTender, tender_source: str) -> float:
    numeric = getattr(tender, "estimated_value_numeric", None)
    if numeric is not None and float(numeric) > 0:
        return float(numeric)
    if tender_source == "federal":
        return _parse_value(getattr(tender, "estimated_value", "") or "")
    return _parse_value(getattr(tender, "value", "") or "")


def _tender_deadline(tender: ConstructionTender, tender_source: str) -> str:
    if tender_source == "federal":
        return getattr(tender, "closing_date", "") or ""
    return getattr(tender, "deadline", "") or ""


def _company_keyword_sources(company: Company) -> list[str]:
    sources = [
        company.name,
        *(company.project_types or []),
        *(company.neighborhoods or []),
        company.google_address or "",
        *(company.trade_tags or []),
        company.dominant_sector or "",
        company.primary_trade or "",
    ]
    return [s for s in sources if s]


def _company_specialization_sources(company: Company) -> list[str]:
    sources: list[str] = []
    for item in company.trade_tags or []:
        if item:
            sources.append(str(item))
    if company.dominant_sector:
        sources.append(company.dominant_sector)
    if company.primary_trade:
        sources.append(company.primary_trade)
    for item in company.project_types or []:
        if item:
            sources.append(str(item))
    return sources


def _company_location_sources(company: Company) -> list[str]:
    """City/region tokens only — excludes street addresses per constitution III."""
    areas: list[str] = []
    for source in (
        company.neighborhoods or [],
        [company.primary_city] if company.primary_city else [],
        [company.primary_province] if company.primary_province else [],
        [company.geographic_reach] if company.geographic_reach else [],
    ):
        for item in source:
            text = str(item).strip()
            if text and text not in areas:
                areas.append(text)
    return areas


def _is_street_level_token(token: str) -> bool:
    if len(token) < 4:
        return True
    if token.isdigit():
        return True
    return bool(_STREET_TOKEN_RE.search(token))


def _build_keyword_sets(company: Company) -> tuple[set[str], set[str]]:
    root: set[str] = set()
    expanded: set[str] = set()
    for source in _company_keyword_sources(company):
        for token in tokenize(source):
            root.add(token)
            expanded.add(token)
    for token in list(root):
        for synonym in KEYWORD_EXPANSIONS.get(token, []):
            expanded.add(synonym)
    return root, expanded


def _is_derived_from_root(token: str, root: set[str]) -> bool:
    for r in root:
        if token in KEYWORD_EXPANSIONS.get(r, []):
            return True
    return False


def score_keywords(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    root, expanded = _build_keyword_sets(company)
    haystack = _tender_haystack(tender, tender_source).lower()
    tender_tokens = set(tokenize(haystack))
    matched: list[str] = []

    for token in tender_tokens:
        if token not in expanded:
            continue
        from_company = (
            token in root
            or _is_derived_from_root(token, root)
            or token not in GENERIC_TENDER_TOKENS
        )
        if from_company:
            matched.append(token)

    points = min(MAX_KEYWORDS, len(matched) * 9)
    if matched:
        detail = f"Matched: {', '.join(matched[:6])}"
        if len(matched) > 6:
            detail += f" +{len(matched) - 6} more"
    else:
        detail = "No company-specific keyword overlap in title, category, or location"

    return BreakdownFactor(
        factor="keywords",
        label="Industry keyword match",
        points=points,
        max_points=MAX_KEYWORDS,
        detail=detail,
    )


def score_category(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    haystack_tokens = set(tokenize(_tender_haystack(tender, tender_source)))
    matched: list[str] = []
    for project_type in company.project_types or []:
        type_tokens = tokenize(project_type)
        if any(token in haystack_tokens for token in type_tokens):
            matched.append(project_type)

    points = min(MAX_CATEGORY, len(matched) * 10)
    if matched:
        detail = (
            f"Permit types include {', '.join(matched[:3])}"
            f"{f' +{len(matched) - 3} more' if len(matched) > 3 else ''} · "
            f"tender: {tender.category or 'Uncategorized'}"
        )
    else:
        detail = "No permit project type overlap with tender category or title"

    return BreakdownFactor(
        factor="category",
        label="Similar project category",
        points=points,
        max_points=MAX_CATEGORY,
        detail=detail,
    )


def score_specialization(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    sources = _company_specialization_sources(company)
    haystack = _tender_haystack(tender, tender_source).lower()
    matched: list[str] = []
    for specialization in sources:
        spec_tokens = tokenize(specialization)
        if any(token in haystack for token in spec_tokens):
            matched.append(specialization)

    points = min(MAX_SPECIALIZATION, len(matched) * 8)
    if matched:
        detail = f"Matched specializations: {', '.join(matched[:3])}"
    else:
        detail = "No trade tag or specialization overlap with tender text"

    return BreakdownFactor(
        factor="specialization",
        label="Domain specialization",
        points=points,
        max_points=MAX_SPECIALIZATION,
        detail=detail,
    )


def score_location(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    location_tokens: set[str] = set()
    for source in _company_location_sources(company):
        for token in tokenize_geo(source):
            if not _is_street_level_token(token):
                location_tokens.add(token)

    if not location_tokens:
        return BreakdownFactor(
            factor="location",
            label="Service area / geography",
            points=0,
            max_points=MAX_LOCATION,
            detail="No city or region service-area data on company profile",
        )

    haystack = _tender_haystack(tender, tender_source).lower()
    matched = [loc for loc in sorted(location_tokens) if loc in haystack]
    points = min(MAX_LOCATION, len(matched) * 5)
    if matched:
        detail = f"Overlap with {', '.join(matched[:4])} in tender text"
    else:
        detail = "No city or regional overlap between service areas and tender location"

    return BreakdownFactor(
        factor="location",
        label="Service area / geography",
        points=points,
        max_points=MAX_LOCATION,
        detail=detail,
    )


def _format_compact_cad(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${round(value / 1_000)}K"
    return f"${round(value):,}"


def score_value_fit(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    tender_value = _tender_value(tender, tender_source)
    avg = float(company.avg_project_value or 0)

    if tender_value <= 0 or avg <= 0:
        return BreakdownFactor(
            factor="value",
            label="Project value range",
            points=0,
            max_points=MAX_VALUE,
            detail="Tender or company value unknown — no value-fit credit",
        )

    ratio = tender_value / avg
    tender_label = _format_compact_cad(tender_value)
    avg_label = f"{_format_compact_cad(avg)} avg"

    if 0.2 <= ratio <= 5:
        points = MAX_VALUE
        detail = f"Tender {tender_label} aligns with company {avg_label} project size ({ratio:.1f}×)"
    elif 0.05 <= ratio <= 20:
        points = 9
        detail = f"Tender {tender_label} within broader range vs {avg_label} ({ratio:.1f}×)"
    elif 0.01 <= ratio <= 100:
        points = 3
        detail = f"Tender {tender_label} outside typical range ({avg_label}, {ratio:.1f}×)"
    else:
        points = 0
        detail = f"Value mismatch vs company {avg_label} ({ratio:.1f}×)"

    return BreakdownFactor(
        factor="value",
        label="Project value range",
        points=points,
        max_points=MAX_VALUE,
        detail=detail,
    )


def _has_relevance_signal(factors: list[BreakdownFactor]) -> bool:
    by_id = {f.factor: f for f in factors}
    keywords = by_id.get("keywords")
    category = by_id.get("category")
    specialization = by_id.get("specialization")
    location = by_id.get("location")
    return bool(
        (keywords and keywords.points > 0)
        or (category and category.points > 0)
        or (specialization and specialization.points > 0)
        or (location and location.points >= 5)
    )


def score_reliability(company: Company, factors: list[BreakdownFactor]) -> BreakdownFactor:
    relevance = _has_relevance_signal(factors)
    reliability = company.ai_reliability_score

    if not relevance:
        return BreakdownFactor(
            factor="reliability",
            label="Company reliability",
            points=0,
            max_points=MAX_RELIABILITY,
            detail="No relevance signal — reliability credit withheld",
        )
    if reliability is None:
        return BreakdownFactor(
            factor="reliability",
            label="Company reliability",
            points=0,
            max_points=MAX_RELIABILITY,
            detail="No AI reliability score — no reliability credit",
        )

    points = round((reliability / 100) * MAX_RELIABILITY)
    return BreakdownFactor(
        factor="reliability",
        label="Company reliability",
        points=points,
        max_points=MAX_RELIABILITY,
        detail=f"AI reliability {reliability}/100 contributes {points} pts",
    )


def score_freshness(tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    deadline_str = _tender_deadline(tender, tender_source)
    if not deadline_str:
        return BreakdownFactor(
            factor="freshness",
            label="Tender deadline",
            points=0,
            max_points=MAX_FRESHNESS,
            detail="No deadline listed — no freshness credit",
        )

    parsed = _parse_date(deadline_str)
    today = datetime.now(timezone.utc).date()
    if parsed is None:
        return BreakdownFactor(
            factor="freshness",
            label="Tender deadline",
            points=0,
            max_points=MAX_FRESHNESS,
            detail="Deadline unavailable — no freshness credit",
        )

    days_left = (parsed - today).days
    if days_left < 0:
        return BreakdownFactor(
            factor="freshness",
            label="Tender deadline",
            points=0,
            max_points=MAX_FRESHNESS,
            detail=f"Closed {abs(days_left)} day{'s' if abs(days_left) != 1 else ''} ago",
        )
    if days_left <= 30:
        return BreakdownFactor(
            factor="freshness",
            label="Tender deadline",
            points=MAX_FRESHNESS,
            max_points=MAX_FRESHNESS,
            detail=f"Closes in {days_left} day{'s' if days_left != 1 else ''} — urgent window",
        )
    return BreakdownFactor(
        factor="freshness",
        label="Tender deadline",
        points=7,
        max_points=MAX_FRESHNESS,
        detail=f"Closes {deadline_str} ({days_left} days remaining)",
    )


@dataclass
class ScoredConstructionMatch:
    score: int
    breakdown: list[BreakdownFactor] = field(default_factory=list)
    breakdown_json: dict[str, Any] = field(default_factory=dict)
    api_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    match_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "breakdown": self.breakdown_json,
            "api_breakdown": self.api_breakdown,
            "match_reason": self.match_reason,
        }


def build_match_reason(factors: list[BreakdownFactor]) -> str:
    active = [f for f in factors if f.points > 0]
    active.sort(key=lambda f: f.points, reverse=True)
    if not active:
        return "Limited alignment across scoring components"
    return "; ".join(f.label for f in active[:3])


def build_fallback_explanation(factors: list[BreakdownFactor]) -> str:
    reasons = build_reasons(factors, limit=3)
    if not reasons:
        return "Limited alignment between company profile and tender requirements."
    joined = ", ".join(reasons[:2])
    return f"Match driven by {joined.lower()}."


def breakdown_json_to_api_breakdown(stored: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return breakdown_json_to_api_breakdown_generic(stored, key_order=CANONICAL_KEYS)


def score_construction_match(
    company: Company,
    tender: ConstructionTender,
    tender_source: str,
) -> ScoredConstructionMatch:
    partial = [
        score_keywords(company, tender, tender_source),
        score_category(company, tender, tender_source),
        score_specialization(company, tender, tender_source),
        score_location(company, tender, tender_source),
        score_value_fit(company, tender, tender_source),
    ]
    partial.append(score_reliability(company, partial))
    partial.append(score_freshness(tender, tender_source))

    total = sum(f.points for f in partial)
    total = max(0, min(100, total))

    factors_by_key = {f.factor: f for f in partial}
    breakdown_json = {f.factor: _factor_to_json(f) for f in partial}
    api_breakdown = to_api_breakdown_seven_key(factors_by_key, key_order=CANONICAL_KEYS)
    assert_score_equals_breakdown(total, api_breakdown)

    return ScoredConstructionMatch(
        score=total,
        breakdown=partial,
        breakdown_json=breakdown_json,
        api_breakdown=api_breakdown,
        match_reason=build_match_reason(partial),
    )
