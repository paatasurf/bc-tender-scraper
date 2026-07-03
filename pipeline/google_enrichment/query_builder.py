"""Search query construction for Google enrichment lookups."""

from __future__ import annotations

import re

from db.models import Company

_STREET_SUFFIX = re.compile(r"^https?://", re.IGNORECASE)
_WWW = re.compile(r"^www\.", re.IGNORECASE)


def _clean_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _best_name(company: Company) -> str:
    for value in (company.name, company.canonical_vendor_name):
        text = _clean_text(value)
        if text:
            return text
    return ""


def _best_city(company: Company) -> str:
    city = _clean_text(company.primary_city)
    if city:
        return city
    address = _clean_text(company.google_address or company.primary_address)
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[1]
    return ""


def _best_province(company: Company) -> str:
    province = _clean_text(company.primary_province)
    return province or "BC"


def _best_address(company: Company) -> str:
    for value in (company.primary_address, company.google_address):
        text = _clean_text(value)
        if text:
            return text
    return ""


def _best_phone(company: Company) -> str:
    return _clean_text(company.google_phone)


def _website_domain(company: Company) -> str:
    for value in (company.website, company.google_website):
        text = _clean_text(value)
        if not text:
            continue
        text = _STREET_SUFFIX.sub("", text.lower())
        text = _WWW.sub("", text)
        return text.split("/")[0]
    return ""


def build_search_query(company: Company) -> str:
    """Build provider search query using architecture priority order."""
    name = _best_name(company)
    city = _best_city(company)
    province = _best_province(company)
    address = _best_address(company)

    if address and city:
        street = address.split(",")[0].strip()
        return f"{name} {street} {city} {province}".strip()
    if city:
        return f"{name} {city} {province}".strip()
    domain = _website_domain(company)
    if domain:
        return f"{name} {domain}".strip()
    return f"{name} BC Canada".strip()


def build_refresh_query(company: Company) -> str:
    """Prefer Place ID lookup when already linked."""
    place_id = _clean_text(company.google_place_id)
    if place_id:
        return f"place_id:{place_id}"
    return build_search_query(company)


def company_match_context(company: Company) -> "CompanyMatchContext":
    from pipeline.google_enrichment.models import CompanyMatchContext

    return CompanyMatchContext(
        company_id=company.id,
        name=_best_name(company),
        city=_best_city(company),
        province=_best_province(company),
        address=_best_address(company),
        phone=_best_phone(company),
    )
