"""Shared deterministic matching helpers for verification providers."""

from __future__ import annotations

from db.models import Company
from pipeline.company_classification import parse_name
from pipeline.company_matching import normalize_vendor_name
from pipeline.registry_verification.city_normalize import extract_city_from_address, normalize_city


def company_normalized_name(company: Company) -> str:
    raw = (company.display_name or company.name or "").strip()
    parsed = parse_name(raw)
    label = parsed["dba"] or parsed["legal"] or raw
    return normalize_vendor_name(label)


def resolve_company_city(company: Company) -> str:
    if (company.primary_city or "").strip():
        return normalize_city(company.primary_city)
    if (company.google_address or "").strip():
        return extract_city_from_address(company.google_address)
    return ""
