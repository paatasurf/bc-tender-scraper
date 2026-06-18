"""Geographic, category, and value overlap — pure functions."""

from __future__ import annotations

import math
import re
from typing import Any

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.competitive_intel.types import CompanyRow
from pipeline.scoring.construction_match_scoring import _parse_city_from_address

_STREET_SUFFIX_RE = re.compile(
    r"\b(street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln|way|court|ct|crescent|cres)\b",
    re.I,
)


def _normalize_city(city: str) -> str:
    return (city or "").strip().lower()


def _is_street_like_token(token: str) -> bool:
    return bool(_STREET_SUFFIX_RE.search(token))


def _city_level_tokens(cip: CompanyIntelligenceProfile, company: CompanyRow) -> set[str]:
    """Primary geo signal: cities from CIP service areas and concentration map."""
    cities: set[str] = set()
    for raw in cip.service_cities:
        city = _normalize_city(raw)
        if city and not _is_street_like_token(city):
            cities.add(city)
    for geo in cip.concentration_map:
        city = _normalize_city(geo.geo)
        if city and not _is_street_like_token(city):
            cities.add(city)
    primary = _normalize_city(getattr(company, "primary_city", "") or "")
    if primary:
        cities.add(primary)
    google_address = getattr(company, "google_address", "") or ""
    if google_address:
        parsed = _parse_city_from_address(google_address)
        if parsed:
            cities.add(_normalize_city(parsed))
    for area in getattr(company, "website_service_areas", None) or []:
        city = _normalize_city(area)
        if city and not _is_street_like_token(city):
            cities.add(city)
    return cities


def _neighborhood_fallback_tokens(cip: CompanyIntelligenceProfile) -> set[str]:
    """Fallback only when city-level tokens are sparse — may include street names."""
    return {_normalize_city(n) for n in cip.neighborhoods if n}


def city_set(cip: CompanyIntelligenceProfile, company: CompanyRow) -> set[str]:
    """City-level geography only (constitution III)."""
    cities = _city_level_tokens(cip, company)
    if not cities:
        cities = _neighborhood_fallback_tokens(cip)
    return cities


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _bhattacharyya(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    return sum(math.sqrt(max(0.0, a.get(k, 0.0)) * max(0.0, b.get(k, 0.0))) for k in keys)


def _token_jaccard(lists_a: list[str], lists_b: list[str]) -> float:
    tokens_a = {_normalize_city(x) for x in lists_a if x}
    tokens_b = {_normalize_city(x) for x in lists_b if x}
    return _jaccard(tokens_a, tokens_b)


def geographic_overlap_raw(
    subject_cip: CompanyIntelligenceProfile,
    peer_cip: CompanyIntelligenceProfile,
    subject: CompanyRow,
    peer: CompanyRow,
) -> tuple[float, str]:
    cities_s = _city_level_tokens(subject_cip, subject)
    cities_p = _city_level_tokens(peer_cip, peer)
    if not cities_s:
        cities_s = _neighborhood_fallback_tokens(subject_cip)
    if not cities_p:
        cities_p = _neighborhood_fallback_tokens(peer_cip)

    city_j = _jaccard(cities_s, cities_p)
    raw = 100.0 * city_j

    s_city = _resolve_primary_city(subject, subject_cip)
    p_city = _resolve_primary_city(peer, peer_cip)
    if s_city and p_city and s_city == p_city:
        raw = min(100.0, raw + 15.0)
        raw = max(raw, 40.0)

    shared = sorted(cities_s & cities_p)[:4]
    if shared:
        detail = f"Shared cities: {', '.join(t.title() for t in shared)}"
    elif s_city and p_city and s_city == p_city:
        detail = f"Shared primary city: {s_city.title()}"
    elif s_city and p_city:
        detail = f"Subject: {s_city.title()}; peer: {p_city.title()}"
    else:
        detail = "No shared cities"
    return raw, detail


def _resolve_primary_city(company: CompanyRow, cip: CompanyIntelligenceProfile) -> str:
    primary = _normalize_city(getattr(company, "primary_city", "") or "")
    if primary:
        return primary
    if cip.service_cities:
        return _normalize_city(cip.service_cities[0])
    google_address = getattr(company, "google_address", "") or ""
    if google_address:
        return _parse_city_from_address(google_address) or ""
    return ""


def category_overlap_raw(
    subject_cip: CompanyIntelligenceProfile,
    peer_cip: CompanyIntelligenceProfile,
    subject: CompanyRow,
    peer: CompanyRow,
) -> tuple[float, str]:
    sf_s = subject_cip.sector_focus or {}
    sf_p = peer_cip.sector_focus or {}
    if sf_s and sf_p:
        coef = _bhattacharyya(sf_s, sf_p)
        raw = min(100.0, 100.0 * coef)
        top = sorted(set(sf_s) & set(sf_p), key=lambda k: min(sf_s.get(k, 0), sf_p.get(k, 0)), reverse=True)[:3]
        detail = f"Sector overlap: {', '.join(top)}" if top else "Sector distributions differ"
        return raw, detail

    types_s = list(getattr(subject, "project_types", None) or []) + list(
        getattr(subject, "award_categories", None) or []
    )
    types_p = list(getattr(peer, "project_types", None) or []) + list(
        getattr(peer, "award_categories", None) or []
    )
    if getattr(peer, "houzz_project_types", None):
        types_p.extend(peer.houzz_project_types)
    j = _token_jaccard(types_s, types_p)
    raw = 100.0 * j
    detail = "Project type overlap (fallback)" if j > 0 else "No category overlap"
    return raw, detail


def _median_value(cip: CompanyIntelligenceProfile, company: CompanyRow) -> float:
    med = cip.value_range.median if cip.value_range else 0.0
    if med > 0:
        return med
    return float(getattr(company, "avg_project_value", 0) or 0)


def value_overlap_raw(
    subject_cip: CompanyIntelligenceProfile,
    peer_cip: CompanyIntelligenceProfile,
    subject: CompanyRow,
    peer: CompanyRow,
) -> tuple[float, str]:
    med_s = _median_value(subject_cip, subject)
    med_p = _median_value(peer_cip, peer)
    if med_s <= 0 or med_p <= 0:
        return 0.0, "Insufficient value data"

    log_dist = abs(math.log10(med_s) - math.log10(med_p))
    raw = max(0.0, 100.0 - 25.0 * log_dist)

    p25_s = subject_cip.value_range.p25 if subject_cip.value_range else 0.0
    p75_s = subject_cip.value_range.p75 if subject_cip.value_range else 0.0
    p25_p = peer_cip.value_range.p25 if peer_cip.value_range else 0.0
    p75_p = peer_cip.value_range.p75 if peer_cip.value_range else 0.0
    if p25_s > 0 and p75_s > 0 and p25_p > 0 and p75_p > 0:
        if p25_s <= p75_p and p25_p <= p75_s:
            raw = min(100.0, raw + 15.0)

    detail = f"Median ${med_s:,.0f} vs ${med_p:,.0f}"
    return raw, detail


def similarity_pre_score(geo_raw: float, cat_raw: float, val_raw: float) -> float:
    return round(0.35 * cat_raw + 0.35 * geo_raw + 0.30 * val_raw, 2)


def shares_geography(
    subject_cip: CompanyIntelligenceProfile,
    peer_cip: CompanyIntelligenceProfile,
    subject: CompanyRow,
    peer: CompanyRow,
) -> bool:
    cities_s = city_set(subject_cip, subject)
    cities_p = city_set(peer_cip, peer)
    if cities_s & cities_p:
        return True
    s_city = _resolve_primary_city(subject, subject_cip)
    p_city = _resolve_primary_city(peer, peer_cip)
    return bool(s_city and p_city and s_city == p_city)
