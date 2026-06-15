"""Deterministic architecture firm ↔ tender match scoring (constitution-compliant)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from db.models import ArchCompany, ArchTender
from pipeline.company_intelligence import _parse_value
from pipeline.scoring.explain import BreakdownFactor, build_reasons
from pipeline.scoring.match_scoring_common import (
    _factor_to_json,
    _normalize_text,
    _parse_date,
    _token_set,
    _tokens_overlap,
    assert_score_equals_breakdown,
)

MAX_PROJECT_TYPE = 40
MAX_SPECIALIZATION = 25
MAX_REGION = 15
MAX_VALUE_FIT = 10
MAX_FRESHNESS = 10

CANONICAL_KEYS = ("project_type", "specialization", "region", "value_fit", "freshness")

_BC_REGION_KEYWORDS = (
    "vancouver",
    "burnaby",
    "richmond",
    "surrey",
    "coquitlam",
    "langley",
    "delta",
    "new westminster",
    "north vancouver",
    "west vancouver",
    "victoria",
    "kelowna",
    "kamloops",
    "nanaimo",
    "fraser valley",
    "okanagan",
    "sea to sky",
    "tri-cities",
    "bc",
    "british columbia",
)


def _fuzzy_type_match(category: str, type_tags: list[str]) -> str | None:
    """Return first matching company type tag for tender category, if any."""
    cat_norm = _normalize_text(category)
    if not cat_norm:
        return None
    cat_tokens = _token_set(category)
    best: str | None = None
    best_overlap = 0
    for tag in type_tags:
        tag_norm = _normalize_text(tag)
        if not tag_norm:
            continue
        if tag_norm in cat_norm or cat_norm in tag_norm:
            return tag
        overlap = len(cat_tokens & _token_set(tag))
        if overlap > best_overlap:
            best_overlap = overlap
            best = tag
    return best if best_overlap >= 1 else None


def _tender_location_text(tender: ArchTender) -> str:
    parts = [tender.company or "", tender.title or "", tender.category or ""]
    return " ".join(p for p in parts if p).strip()


def _company_type_tags(company: ArchCompany) -> list[str]:
    tags: list[str] = []
    for source in (company.project_types or [], company.houzz_project_types or []):
        for item in source:
            text = str(item).strip()
            if text and text not in tags:
                tags.append(text)
    return tags


def _company_specialization_sources(company: ArchCompany) -> list[str]:
    sources: list[str] = []
    for item in company.website_specializations or []:
        if item:
            sources.append(str(item))
    if company.dominant_sector:
        sources.append(company.dominant_sector)
    for item in company.trade_tags or []:
        if item:
            sources.append(str(item))
    return sources


def _company_service_areas(company: ArchCompany) -> list[str]:
    areas: list[str] = []
    for source in (
        company.neighborhoods or [],
        company.houzz_service_areas or [],
        company.website_service_areas or [],
    ):
        for item in source:
            text = str(item).strip()
            if text and text not in areas:
                areas.append(text)
    if company.geographic_reach:
        areas.append(company.geographic_reach)
    return areas


def score_project_type(company: ArchCompany, tender: ArchTender) -> BreakdownFactor:
    tags = _company_type_tags(company)
    matched_tag = _fuzzy_type_match(tender.category or tender.title, tags)
    if not matched_tag:
        return BreakdownFactor(
            factor="project_type",
            label="Project type experience",
            points=0,
            max_points=MAX_PROJECT_TYPE,
            detail="No matching project type in firm portfolio",
        )

    experience_count = int(company.total_projects or 0)
    if experience_count <= 2:
        points = 15
        detail = f"Limited experience ({experience_count} projects); category aligns with '{matched_tag}'"
    elif experience_count <= 10:
        points = 28
        detail = f"Moderate experience ({experience_count} projects) in '{matched_tag}'"
    else:
        points = MAX_PROJECT_TYPE
        detail = f"Strong experience ({experience_count} projects) in '{matched_tag}'"

    return BreakdownFactor(
        factor="project_type",
        label="Project type experience",
        points=points,
        max_points=MAX_PROJECT_TYPE,
        detail=detail,
    )


def score_specialization(company: ArchCompany, tender: ArchTender) -> BreakdownFactor:
    category = tender.category or tender.title or ""
    sources = _company_specialization_sources(company)
    if not category or not sources:
        return BreakdownFactor(
            factor="specialization",
            label="Specialization match",
            points=0,
            max_points=MAX_SPECIALIZATION,
            detail="Insufficient category or specialization data",
        )

    matching_sources = sum(1 for src in sources if _tokens_overlap(category, src))
    if matching_sources >= 2:
        points = MAX_SPECIALIZATION
        detail = "Category aligns with multiple firm specialization signals"
    elif matching_sources == 1:
        points = 12
        detail = "Partial specialization overlap with tender category"
    else:
        points = 0
        detail = "No specialization overlap with tender category"

    return BreakdownFactor(
        factor="specialization",
        label="Specialization match",
        points=points,
        max_points=MAX_SPECIALIZATION,
        detail=detail,
    )


def score_region(company: ArchCompany, tender: ArchTender) -> BreakdownFactor:
    tender_text = _normalize_text(_tender_location_text(tender))
    areas = _company_service_areas(company)
    if not tender_text or not areas:
        return BreakdownFactor(
            factor="region",
            label="Region match",
            points=0,
            max_points=MAX_REGION,
            detail="No city/region data for firm or tender",
        )

    direct_matches = [
        area for area in areas if _normalize_text(area) and _normalize_text(area) in tender_text
    ]
    if direct_matches:
        return BreakdownFactor(
            factor="region",
            label="Region match",
            points=MAX_REGION,
            max_points=MAX_REGION,
            detail=f"Tender region matches service area: {', '.join(direct_matches[:3])}",
        )

    tender_keywords = {kw for kw in _BC_REGION_KEYWORDS if kw in tender_text}
    area_text = _normalize_text(" ".join(areas))
    area_keywords = {kw for kw in _BC_REGION_KEYWORDS if kw in area_text}
    shared = tender_keywords & area_keywords
    if shared:
        return BreakdownFactor(
            factor="region",
            label="Region match",
            points=8,
            max_points=MAX_REGION,
            detail=f"Shared BC region signal: {', '.join(sorted(shared)[:3])}",
        )

    return BreakdownFactor(
        factor="region",
        label="Region match",
        points=0,
        max_points=MAX_REGION,
        detail="No city or regional overlap at district level",
    )


def score_value_fit(company: ArchCompany, tender: ArchTender) -> BreakdownFactor:
    tender_value = _parse_value(tender.value)
    avg = float(company.avg_project_value or 0)
    p25 = company.value_p25
    p75 = company.value_p75

    if tender_value <= 0 or (avg <= 0 and p25 is None and p75 is None):
        return BreakdownFactor(
            factor="value_fit",
            label="Budget / value fit",
            points=0,
            max_points=MAX_VALUE_FIT,
            detail="Insufficient tender value or company scale data",
        )

    if p25 is not None and p75 is not None and p25 > 0 and p75 > 0:
        if p25 <= tender_value <= p75:
            return BreakdownFactor(
                factor="value_fit",
                label="Budget / value fit",
                points=MAX_VALUE_FIT,
                max_points=MAX_VALUE_FIT,
                detail=f"Tender value ${tender_value:,.0f} within firm P25–P75 band",
            )

    if avg > 0:
        ratio = tender_value / avg
        if 0.5 <= ratio <= 2.0:
            return BreakdownFactor(
                factor="value_fit",
                label="Budget / value fit",
                points=MAX_VALUE_FIT,
                max_points=MAX_VALUE_FIT,
                detail=f"Tender value within ±50% of typical project (${avg:,.0f} avg)",
            )
        if 0.25 <= ratio <= 4.0:
            return BreakdownFactor(
                factor="value_fit",
                label="Budget / value fit",
                points=5,
                max_points=MAX_VALUE_FIT,
                detail=f"Tender value within 2× of typical project scale (${avg:,.0f} avg)",
            )

    return BreakdownFactor(
        factor="value_fit",
        label="Budget / value fit",
        points=0,
        max_points=MAX_VALUE_FIT,
        detail="Tender value outside firm typical project scale",
    )


def score_freshness(tender: ArchTender) -> BreakdownFactor:
    parsed = _parse_date(tender.deadline)
    today = datetime.now(timezone.utc).date()

    if parsed is None:
        return BreakdownFactor(
            factor="freshness",
            label="Deadline freshness",
            points=2,
            max_points=MAX_FRESHNESS,
            detail="Deadline missing or unparseable",
        )

    if parsed < today:
        return BreakdownFactor(
            factor="freshness",
            label="Deadline freshness",
            points=0,
            max_points=MAX_FRESHNESS,
            detail=f"Deadline expired ({parsed.isoformat()})",
        )

    days_out = (parsed - today).days
    if days_out > 14:
        return BreakdownFactor(
            factor="freshness",
            label="Deadline freshness",
            points=MAX_FRESHNESS,
            max_points=MAX_FRESHNESS,
            detail=f"Deadline {days_out} days away",
        )

    return BreakdownFactor(
        factor="freshness",
        label="Deadline freshness",
        points=7,
        max_points=MAX_FRESHNESS,
        detail=f"Deadline approaching ({days_out} days away)",
    )


@dataclass
class ScoredArchMatch:
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


def to_api_breakdown(factors: list[BreakdownFactor]) -> dict[str, dict[str, Any]]:
    """Map five canonical components to the 7-key API shape for frontend compatibility."""
    by_id = {f.factor: f for f in factors}
    project = by_id.get("project_type")
    spec = by_id.get("specialization")
    region = by_id.get("region")
    value = by_id.get("value_fit")
    fresh = by_id.get("freshness")

    def item(factor: BreakdownFactor | None, default_detail: str) -> dict[str, Any]:
        if factor is None:
            return {"points": 0, "detail": default_detail}
        return {"points": factor.points, "detail": factor.detail}

    return {
        "keywords": {"points": 0, "detail": "Included in project type score"},
        "category": item(project, "No project type match"),
        "specialization": item(spec, "No specialization match"),
        "location": item(region, "No region match"),
        "value": item(value, "No value fit signal"),
        "reliability": {"points": 0, "detail": "Not used for architecture matching"},
        "freshness": item(fresh, "No deadline signal"),
    }


def breakdown_json_to_api_breakdown(stored: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Reconstruct API breakdown from persisted canonical JSON."""
    if not stored:
        return to_api_breakdown([])

    factors = [
        BreakdownFactor(
            factor=key,
            label=key.replace("_", " ").title(),
            points=int(stored[key].get("points", 0)),
            max_points=int(stored[key].get("max_points", 0)),
            detail=str(stored[key].get("detail", "")),
        )
        for key in CANONICAL_KEYS
        if key in stored and isinstance(stored[key], dict)
    ]
    return to_api_breakdown(factors)


def build_match_reason(factors: list[BreakdownFactor]) -> str:
    active = [f for f in factors if f.points > 0]
    active.sort(key=lambda f: f.points, reverse=True)
    if not active:
        return "Limited alignment across scoring components"
    labels = [f.label for f in active[:3]]
    return "; ".join(labels)


def build_fallback_explanation(factors: list[BreakdownFactor]) -> str:
    reasons = build_reasons(factors, limit=3)
    if not reasons:
        return "Limited alignment between firm profile and tender requirements."
    joined = ", ".join(reasons[:2])
    return f"Match driven by {joined.lower()}."


def score_architecture_match(company: ArchCompany, tender: ArchTender) -> ScoredArchMatch:
    factors = [
        score_project_type(company, tender),
        score_specialization(company, tender),
        score_region(company, tender),
        score_value_fit(company, tender),
        score_freshness(tender),
    ]
    total = sum(f.points for f in factors)
    if total > 100:
        raise ValueError(f"Architecture match score exceeds 100: {total}")

    breakdown_json = {f.factor: _factor_to_json(f) for f in factors}
    api_breakdown = to_api_breakdown(factors)
    assert_score_equals_breakdown(total, api_breakdown)

    return ScoredArchMatch(
        score=total,
        breakdown=factors,
        breakdown_json=breakdown_json,
        api_breakdown=api_breakdown,
        match_reason=build_match_reason(factors),
    )
