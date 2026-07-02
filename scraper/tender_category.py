from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from scraper.utils import clean_text

CONSTRUCTION = "Construction"
SERVICES = "Services"

CANADABUYS_SOURCE = "buyandsell.gc.ca"
MERX_SOURCE = "merx.com"


@dataclass(frozen=True)
class TitleCategoryRule:
    """Ordered title rule used for MERX and federal fallback classification."""

    rule_id: str
    pattern: re.Pattern[str]
    category: str


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Checked first: keep physical construction / trade scope as Construction even when
# titles contain "services" or "consulting".
CONSTRUCTION_TITLE_OVERRIDES: tuple[TitleCategoryRule, ...] = (
    TitleCategoryRule("design_build", _compile(r"\bdesign[\s-]build\b"), CONSTRUCTION),
    TitleCategoryRule("general_contracting", _compile(r"\bgeneral contracting services\b"), CONSTRUCTION),
    TitleCategoryRule("on_call_general_contracting", _compile(r"\bon-?call general contracting\b"), CONSTRUCTION),
    TitleCategoryRule("janitorial", _compile(r"\bjanitorial services\b"), CONSTRUCTION),
    TitleCategoryRule("piping_installation", _compile(r"\bpiping installation services\b"), CONSTRUCTION),
    TitleCategoryRule(
        "painting_graffiti",
        _compile(r"\bpainting and graffiti removal services\b"),
        CONSTRUCTION,
    ),
    TitleCategoryRule(
        "construction_management_services",
        _compile(r"\bconstruction management services\b(?!\s+for\b.*\bconsulting\b)"),
        CONSTRUCTION,
    ),
    TitleCategoryRule(
        "building_maintenance_services",
        _compile(r"\bbuilding maintenance services\b"),
        CONSTRUCTION,
    ),
    TitleCategoryRule(
        "physical_restoration_services",
        _compile(r"\brestoration services\b(?![\s\S]*\bconsulting\b)"),
        CONSTRUCTION,
    ),
    TitleCategoryRule(
        "habitat_restoration_services",
        _compile(r"\bhabitat restoration services\b"),
        CONSTRUCTION,
    ),
    TitleCategoryRule(
        "engine_replacement_services",
        _compile(r"\breplacement services\b"),
        CONSTRUCTION,
    ),
)

# Professional / A&E procurement signals. Order matters: more specific phrases first.
PROFESSIONAL_SERVICES_TITLE_RULES: tuple[TitleCategoryRule, ...] = (
    TitleCategoryRule("prime_consultant_services", _compile(r"\bprime consultant services\b"), SERVICES),
    TitleCategoryRule("prime_consultant", _compile(r"\bprime consultant\b"), SERVICES),
    TitleCategoryRule("architectural_services", _compile(r"\barchitectural services\b"), SERVICES),
    TitleCategoryRule(
        "architectural_and_engineering_services",
        _compile(r"\barchitectural and engineering services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "architecture_engineering_landscape",
        _compile(r"\barchitecture,\s*engineering and landscaping design services\b"),
        SERVICES,
    ),
    TitleCategoryRule("ae_consulting_services", _compile(r"\bA/?E consulting services\b"), SERVICES),
    TitleCategoryRule(
        "engineering_consulting_services",
        _compile(r"\bengineering consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "consulting_engineering_services",
        _compile(r"\bconsulting engineering services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "engineering_services",
        _compile(
            r"\b(?:bridge|civil|structural|mechanical|electrical|transportation|"
            r"environmental|rotational)?\s*engineering services\b"
        ),
        SERVICES,
    ),
    TitleCategoryRule("surveying_geomatics", _compile(r"\bsurveying and geomatics services\b"), SERVICES),
    TitleCategoryRule("environmental_consulting", _compile(r"\benvironmental consulting services\b"), SERVICES),
    TitleCategoryRule(
        "hazardous_materials_consulting",
        _compile(r"\bhazardous materials consulting\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "archaeological_consulting",
        _compile(r"\barchaeological consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule("consulting_services", _compile(r"\bconsulting services\b"), SERVICES),
    TitleCategoryRule("consultant_services", _compile(r"\bconsultant services\b"), SERVICES),
    TitleCategoryRule(
        "professional_consulting_services",
        _compile(r"\bprofessional consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "professional_services",
        _compile(r"\bprofessional services\b(?![\s\S]*\bbuilding maintenance services\b)"),
        SERVICES,
    ),
    TitleCategoryRule("design_services", _compile(r"\bdesign services\b"), SERVICES),
    TitleCategoryRule(
        "civil_engineering_support",
        _compile(r"\bgeneral civil engineering support\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "civil_engineering_consulting",
        _compile(r"\bcivil engineering consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule("instructional_design", _compile(r"\binstructional design services\b"), SERVICES),
    TitleCategoryRule(
        "mechanical_consulting_standing",
        _compile(r"\bmechanical consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "design_consultant_services",
        _compile(r"\bdesign consultant services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "technical_consulting_services",
        _compile(r"\bdesign and technical consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "consulting_and_professional_services",
        _compile(r"\bconsulting and professional services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "benefits_consulting",
        _compile(r"\bbenefits consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "actuarial_consulting",
        _compile(r"\bactuarial valuation consulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "hr_consulting",
        _compile(r"\bhuman resources\b.*\bconsulting services\b"),
        SERVICES,
    ),
    TitleCategoryRule(
        "resource_management_services",
        _compile(r"\bresource management services\b"),
        SERVICES,
    ),
)


def _first_matching_rule(title: str, rules: Iterable[TitleCategoryRule]) -> TitleCategoryRule | None:
    for rule in rules:
        if rule.pattern.search(title):
            return rule
    return None


def normalize_federal_procurement_category(raw_category: str) -> str | None:
    """Map CanadaBuys listing category labels to dashboard categories."""
    text = clean_text(raw_category).lower()
    if not text:
        return None
    if text == SERVICES.lower() or text.startswith("services"):
        return SERVICES
    if CONSTRUCTION.lower() in text:
        return CONSTRUCTION
    return None


def classify_title_category(title: str) -> str | None:
    """Classify from title phrase rules; returns None when no rule matches."""
    normalized = clean_text(title)
    if not normalized:
        return None

    override = _first_matching_rule(normalized, CONSTRUCTION_TITLE_OVERRIDES)
    if override:
        return override.category

    match = _first_matching_rule(normalized, PROFESSIONAL_SERVICES_TITLE_RULES)
    if match:
        return match.category

    return None


def resolve_tender_category(
    *,
    title: str,
    source: str = "",
    raw_category: str = "",
) -> str:
    """
    Resolve the dashboard category for a federal/MERX tender.

    Priority:
    1. Trust CanadaBuys listing category when present.
    2. Apply construction override title rules.
    3. Apply professional-services title rules (MERX and federal fallback).
    4. Default to Construction (legacy MERX behaviour).
    """
    source_key = clean_text(source).lower()

    if source_key == CANADABUYS_SOURCE:
        mapped = normalize_federal_procurement_category(raw_category)
        if mapped:
            return mapped
        title_category = classify_title_category(title)
        if title_category:
            return title_category
        return CONSTRUCTION

    if source_key == MERX_SOURCE:
        title_category = classify_title_category(title)
        if title_category:
            return title_category
        return CONSTRUCTION

    mapped = normalize_federal_procurement_category(raw_category)
    if mapped:
        return mapped
    title_category = classify_title_category(title)
    if title_category:
        return title_category
    return raw_category or CONSTRUCTION
