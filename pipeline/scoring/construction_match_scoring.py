"""Deterministic construction company ↔ tender match scoring (constitution-compliant)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from db.models import CommercialTender, Company, Tender
from pipeline.scoring.explain import BreakdownFactor, build_reasons
from pipeline.scoring.match_scoring_common import (
    STOP_WORDS,
    _factor_to_json,
    _normalize_text,
    _parse_date,
    _token_set,
    assert_score_equals_breakdown,
    breakdown_json_to_api_breakdown_generic,
    tokenize,
    to_api_breakdown,
)

ConstructionTender = Tender | CommercialTender

MAX_KEYWORDS = 35
MAX_CATEGORY = 20
MAX_SPECIALIZATION = 15
MAX_SCOPE = 14
MAX_LOCATION = 15
MAX_VALUE = 15
MAX_BUYER = 15
MAX_RELIABILITY = 5
MAX_FRESHNESS = 10

# F005 Phase 2 region-fit sub-scores (folded into the "location" factor).
REGION_SAME_REGION = 8
REGION_OUT_OF_REGION_PENALTY = 12
REGION_UNDETERMINED_TENDER_SOFT_PENALTY = 5

CANONICAL_KEYS = (
    "keywords",
    "category",
    "specialization",
    "scope",
    "location",
    "value",
    "buyer",
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

# Work-type gate (F005 Phase 2): region and value bonuses are withheld unless
# the tender title/category contains a real construction-scope signal.
_CONSTRUCTION_SCOPE_RE = re.compile(
    r"\b("
    r"building|construction|renovation|addition|alteration"
    r"|tenant.?improvement|fit.?out"
    r"|civil.?construction|infrastructure.?construction"
    r"|mechanical.?construction|electrical.?construction"
    r"|structural|roofing|excavation|concrete"
    r"|watermain|storm.?sewer|sanitary.?sewer"
    r"|utility.?construction|substation.?construction"
    r"|roadway|road\s+rehabilitation|road\s+improvement|paving|resurfacing"
    r"|wastewater|water\s+treatment|treatment\s+plant|water\s+system"
    r"|bridge|culvert|trail\s+construction|transit\s+exchange"
    r"|demolition|channel\s+reconstruction|site\s+servicing"
    r")\b",
    re.I,
)


# Maintenance / non-build contract denylist.
# Tenders whose primary activity is operating, cleaning, or tending existing
# assets — not building or renovating — are suppressed: category, specialisation,
# region, and value bonuses are all zeroed so they cannot surface for a GC.
_MAINTENANCE_CONTRACT_RE = re.compile(
    r"\b("
    r"maintenance\s+services?|facilities?\s+maintenance|grounds?\s+maintenance"
    r"|building\s+maintenance|preventive\s+maintenance|preventative\s+maintenance"
    r"|property\s+maintenance|fleet\s+maintenance"
    r"|janitorial|custodial|cleaning\s+services?|housekeeping"
    r"|landscaping\s+services?|grounds?\s+keeping|lawn\s+(care|maintenance)"
    r"|snow\s+removal|pest\s+control"
    r")\b",
    re.I,
)


def _is_maintenance_contract(tender: ConstructionTender) -> bool:
    """True when the tender title describes an operating/cleaning/maintenance contract."""
    return bool(_MAINTENANCE_CONTRACT_RE.search(tender.title or ""))


def _has_construction_scope(tender: ConstructionTender, tender_source: str) -> bool:
    """True when the tender TITLE contains a construction-scope keyword.

    Deliberately excludes the category field: federal portal category is
    'Construction' for nearly every tender regardless of actual work type, so
    checking it would make the gate a no-op.  For commercial tenders the
    company/source name is also excluded for the same reason.
    """
    return bool(_CONSTRUCTION_SCOPE_RE.search(tender.title or ""))


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

# --- F005 Phase 2: poison-token denylist -------------------------------------
# Tokens that pollute keyword/region matching when derived from a company's
# street-level neighborhood list or address. Street-suffix words, the province
# and country names, and low-signal generic words all match nearly every tender
# (e.g. "columbia" matches "British Columbia" on every federal tender).
STREET_SUFFIX_TOKENS = frozenset(
    {
        "street", "st", "road", "rd", "avenue", "ave", "drive", "dr",
        "boulevard", "blvd", "place", "pl", "lane", "ln", "way", "court", "ct",
        "crescent", "cres", "trail", "terrace", "close", "grove", "row", "walk",
        "mews", "quay", "wynd", "highway", "hwy", "diversion", "connector",
    }
)
GEO_NAME_NOISE_TOKENS = frozenset(
    {"british", "columbia", "canada", "canadian", "province", "provincial", "federal", "national"}
)
GENERIC_NOISE_TOKENS = frozenset({"new", "old", "major", "minor", "main"})
# Geographic place-noun tokens that appear in BC tender titles (e.g. "Cache Creek
# Channel") but carry no trade-skill meaning.  Without this guard they leak from
# company neighborhood/address strings into the keyword set and produce false
# positive matches.
PLACE_NAME_TOKENS = frozenset({
    "creek", "lake", "river", "mountain", "valley", "park", "heights",
    "ridge", "glen", "bay", "cove", "point", "island", "inlet", "harbour",
    "harbor", "forest", "meadow", "hill", "hills", "beach", "shore", "delta",
    "canyon", "bluff", "grove", "hollow", "crossing", "junction", "landing",
})
POISON_TOKENS = STREET_SUFFIX_TOKENS | GEO_NAME_NOISE_TOKENS | GENERIC_NOISE_TOKENS | PLACE_NAME_TOKENS

# --- F005 Phase 2: BC city -> region gazetteer -------------------------------
# Ambiguous common-word place names (hope, golden, mission, trail, midway)
# are intentionally excluded to avoid false region hits in tender titles.
_REGION_LOWER_MAINLAND = "Lower Mainland"
_REGION_VANCOUVER_ISLAND = "Vancouver Island"
_REGION_INTERIOR = "Interior / Okanagan"
_REGION_KOOTENAY = "Kootenay"
_REGION_NORTHERN = "Northern BC"

_CITY_TO_REGION: dict[str, str] = {}


def _register_cities(region: str, cities: tuple[str, ...]) -> None:
    for city in cities:
        _CITY_TO_REGION[city] = region


_register_cities(
    _REGION_LOWER_MAINLAND,
    (
        "vancouver", "burnaby", "surrey", "richmond", "coquitlam", "port coquitlam",
        "port moody", "new westminster", "delta", "langley", "maple ridge",
        "pitt meadows", "north vancouver", "west vancouver", "abbotsford",
        "chilliwack", "white rock", "squamish", "whistler", "pemberton",
        "bowen island", "tsawwassen", "ladner", "fort langley",
    ),
)
_register_cities(
    _REGION_VANCOUVER_ISLAND,
    (
        "victoria", "saanich", "langford", "colwood", "esquimalt", "sidney",
        "nanaimo", "ladysmith", "duncan", "lake cowichan", "parksville",
        "qualicum", "port alberni", "alberni", "courtenay", "comox", "campbell river",
        "powell river", "tofino", "ucluelet", "sooke", "port hardy", "cumberland",
        "sproat lake",
    ),
)
_register_cities(
    _REGION_INTERIOR,
    (
        "kelowna", "west kelowna", "vernon", "penticton", "kamloops",
        "salmon arm", "summerland", "peachland", "lake country", "oliver",
        "osoyoos", "merritt", "princeton", "logan lake", "sicamous", "armstrong",
        "enderby", "keremeos", "okanagan", "cache creek", "chase",
    ),
)
_register_cities(
    _REGION_KOOTENAY,
    (
        "nelson", "castlegar", "new denver", "kaslo", "nakusp", "rossland",
        "fernie", "cranbrook", "kimberley", "creston", "invermere",
        "revelstoke", "sparwood", "elkford", "grand forks", "slocan", "salmo",
    ),
)
_register_cities(
    _REGION_NORTHERN,
    (
        "prince george", "quesnel", "williams lake", "100 mile house",
        "prince rupert", "terrace", "kitimat", "smithers", "fort st john",
        "dawson creek", "fort nelson", "mackenzie", "vanderhoof", "burns lake",
        "houston", "hazelton", "valemount", "fraser lake",
    ),
)

_REGION_ALIASES: dict[str, str] = {
    "lower mainland": _REGION_LOWER_MAINLAND,
    "metro vancouver": _REGION_LOWER_MAINLAND,
    "greater vancouver": _REGION_LOWER_MAINLAND,
    "fraser valley": _REGION_LOWER_MAINLAND,
    "sea to sky": _REGION_LOWER_MAINLAND,
    "vancouver island": _REGION_VANCOUVER_ISLAND,
    "okanagan": _REGION_INTERIOR,
    "thompson": _REGION_INTERIOR,
    "interior": _REGION_INTERIOR,
    "kootenay": _REGION_KOOTENAY,
    "kootenays": _REGION_KOOTENAY,
    "cariboo": _REGION_NORTHERN,
    "northern bc": _REGION_NORTHERN,
    "north coast": _REGION_NORTHERN,
}

# Multiword keys must be probed via substring; single-word via token membership.
_MULTIWORD_CITIES = tuple(sorted((c for c in _CITY_TO_REGION if " " in c), key=len, reverse=True))
_SINGLEWORD_CITIES = frozenset(c for c in _CITY_TO_REGION if " " not in c)

# --- F005 Phase 2: value range parsing ---------------------------------------
_VALUE_NUM_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([kKmMbB]?)")
_VALUE_SCALE = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}


def _parse_value_string(raw: str) -> float:
    """Parse a value/range string to a numeric midpoint.

    Handles '$200K-$800K', '$800K–$2.5M', '$15M-$40M', '$250,000', '2.5M'.
    A range returns its midpoint; a single figure returns itself.
    """
    if not raw:
        return 0.0
    values: list[float] = []
    for num, suffix in _VALUE_NUM_RE.findall(raw):
        try:
            magnitude = float(num.replace(",", ""))
        except ValueError:
            continue
        scaled = magnitude * _VALUE_SCALE.get(suffix.lower(), 1.0)
        if scaled > 0:
            values.append(scaled)
    if not values:
        return 0.0
    if len(values) >= 2:
        return (min(values) + max(values)) / 2.0
    return values[0]


def _is_street_address(text: str) -> bool:
    """True when a neighborhood entry is a street address (contains a street suffix)."""
    return bool(_STREET_TOKEN_RE.search(text or ""))


_PROVINCE_SEGMENT_RE = re.compile(r"\b(BC|British Columbia)\b", re.I)


def _parse_city_from_address(address: str) -> str | None:
    """Extract city from a Canadian address when primary_city is empty.

    e.g. "1827 W 5th Ave, Vancouver, BC V6J 1P5" → "vancouver"
    """
    if not address or not address.strip():
        return None
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if not parts:
        return None

    for index, part in enumerate(parts):
        if _PROVINCE_SEGMENT_RE.search(part) and index > 0:
            candidate = parts[index - 1].strip().lower()
            if candidate and not _is_street_address(candidate):
                return candidate

    if len(parts) >= 3:
        candidate = parts[-2].strip().lower()
        if candidate and not _is_street_address(candidate):
            return candidate

    return None


def _detect_geo(*texts: str) -> tuple[set[str], set[str]]:
    """Return (cities, regions) detected from free text using the BC gazetteer.

    Multiword matches (e.g. "vancouver island", "new denver") consume their
    constituent tokens so a single-word city ("vancouver") can't also fire from
    the same phrase — otherwise "Vancouver Island" would read as Lower Mainland.
    """
    blob = " ".join(_normalize_text(t) for t in texts if t)
    if not blob:
        return set(), set()
    cities: set[str] = set()
    regions: set[str] = set()
    consumed: set[str] = set()
    for city in _MULTIWORD_CITIES:
        if city in blob:
            cities.add(city)
            regions.add(_CITY_TO_REGION[city])
            consumed.update(city.split())
    for alias, region in _REGION_ALIASES.items():
        if alias in blob:
            regions.add(region)
            consumed.update(alias.split())
    tokens = set(blob.split()) - consumed
    for city in _SINGLEWORD_CITIES & tokens:
        cities.add(city)
        regions.add(_CITY_TO_REGION[city])
    return cities, regions


def _company_geo(company: Company) -> tuple[set[str], set[str]]:
    """City/region signal for a company — ignores street-level addresses/neighborhoods."""
    geo_texts: list[str] = []

    if company.primary_city and company.primary_city.strip():
        geo_texts.append(company.primary_city)

    if company.geographic_reach and company.geographic_reach.strip():
        geo_texts.append(company.geographic_reach)

    safe_neighborhoods = [
        n for n in (company.neighborhoods or []) if n and not _is_street_address(str(n))
    ]
    return _detect_geo(*geo_texts, *safe_neighborhoods)


def _tender_geo(tender: ConstructionTender, tender_source: str) -> tuple[set[str], set[str]]:
    if tender_source == "federal":
        return _detect_geo(
            tender.title or "",
            getattr(tender, "organization", "") or "",
            getattr(tender, "location", "") or "",
        )
    return _detect_geo(tender.title or "", getattr(tender, "company", "") or "")


def _clean_tokens(tokens: list[str]) -> list[str]:
    """Drop poison tokens (street suffixes, province/country, generic words)."""
    return [t for t in tokens if t not in POISON_TOKENS]


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
    # AI-estimated budget range is the richest signal on both tender tables
    # (e.g. "$15M–$40M"); the raw value/estimated_value columns are usually empty.
    budget = _parse_value_string(getattr(tender, "ai_budget_estimate", "") or "")
    if budget > 0:
        return budget
    if tender_source == "federal":
        return _parse_value_string(getattr(tender, "estimated_value", "") or "")
    return _parse_value_string(getattr(tender, "value", "") or "")


def _tender_deadline(tender: ConstructionTender, tender_source: str) -> str:
    if tender_source == "federal":
        return getattr(tender, "closing_date", "") or ""
    return getattr(tender, "deadline", "") or ""


def _company_keyword_sources(company: Company) -> list[str]:
    # Only trade-relevant profile fields feed the keyword set.  Addresses and
    # neighborhood strings are deliberately excluded: they inject place-name and
    # street tokens that match geographic text in tender titles, producing false
    # trade-skill matches.  Location signal is handled exclusively by
    # score_location() via _company_geo().
    sources = [
        company.name,
        *(company.project_types or []),
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




def _build_keyword_sets(company: Company) -> tuple[set[str], set[str]]:
    root: set[str] = set()
    expanded: set[str] = set()
    for source in _company_keyword_sources(company):
        for token in _clean_tokens(tokenize(source)):
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
        type_tokens = _clean_tokens(tokenize(project_type))
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
        spec_tokens = _clean_tokens(tokenize(specialization))
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
    """City/region-aware region fit (F005 Phase 2).

    Same city > same region > out-of-region (penalty). Soft penalty when company
    region is known but tender region cannot be resolved.
    """
    company_cities, company_regions = _company_geo(company)
    tender_cities, tender_regions = _tender_geo(tender, tender_source)

    def factor(points: int, detail: str) -> BreakdownFactor:
        return BreakdownFactor(
            factor="location",
            label="Region / service area",
            points=points,
            max_points=MAX_LOCATION,
            detail=detail,
        )

    if not company_regions:
        return factor(0, "Company operating region undetermined — region neutral")
    if not tender_regions:
        return factor(
            -REGION_UNDETERMINED_TENDER_SOFT_PENALTY,
            "Tender region undetermined — applying soft penalty",
        )

    shared_cities = company_cities & tender_cities
    if shared_cities:
        return factor(
            MAX_LOCATION,
            f"Same city as company operating area: {', '.join(sorted(shared_cities)[:2]).title()}",
        )
    if company_regions & tender_regions:
        shared_region = sorted(company_regions & tender_regions)[0]
        return factor(REGION_SAME_REGION, f"Same region as company: {shared_region}")

    return factor(
        -REGION_OUT_OF_REGION_PENALTY,
        f"Out of region — company {sorted(company_regions)[0]} vs tender {sorted(tender_regions)[0]}",
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


def score_scope_alignment(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    """Construction-scope signal: does the tender title/category align with GC work types?

    Mirrors the scope_pts logic in _score_construction_tender_rules (opportunity_discovery)
    so the deterministic breakdown agrees with the rule-scanner's candidate ranking.
    """
    haystack = _tender_haystack(tender, tender_source).lower()
    category = (tender.category or "").lower()

    trade_hit = _has_construction_scope(tender, tender_source)
    cat_hit = any(
        (pt or "").lower() in haystack or (pt or "").lower() in category
        for pt in (company.project_types or [])
        if pt
    )

    if category == "construction" and (trade_hit or cat_hit):
        points = MAX_SCOPE
        detail = (
            "Category=Construction + title keyword overlap"
            if trade_hit
            else "Category=Construction + project type overlap"
        )
    elif trade_hit and cat_hit:
        points = 12
        detail = "Title keyword + project type both signal construction scope"
    elif trade_hit:
        points = 8
        detail = "Title contains construction-scope keyword"
    else:
        points = 0
        detail = "No construction-scope signal in title or category"

    return BreakdownFactor(
        factor="scope",
        label="Construction scope",
        points=points,
        max_points=MAX_SCOPE,
        detail=detail,
    )


def score_buyer_relevance(company: Company, tender: ConstructionTender, tender_source: str) -> BreakdownFactor:
    """Prior buyer relationship or buyer-type fit.

    Mirrors _buyer_relevance_points in opportunity_discovery so the deterministic
    breakdown agrees with the rule-scanner's candidate ranking.
    """
    if tender_source == "federal":
        org = (getattr(tender, "organization", "") or "").strip().lower()
    else:
        org = (getattr(tender, "company", "") or "").strip().lower()

    if not org:
        return BreakdownFactor(
            factor="buyer",
            label="Buyer relevance",
            points=0,
            max_points=MAX_BUYER,
            detail="No issuing organisation listed",
        )

    for client in (company.award_clients or []):
        c = (client or "").strip().lower()
        if c and (c in org or org in c):
            return BreakdownFactor(
                factor="buyer",
                label="Buyer relevance",
                points=MAX_BUYER,
                max_points=MAX_BUYER,
                detail=f"Known buyer: {client[:60]}",
            )

    for level in (company.buyer_levels or []):
        if level and level.lower() in org:
            return BreakdownFactor(
                factor="buyer",
                label="Buyer relevance",
                points=10,
                max_points=MAX_BUYER,
                detail=f"Buyer type match: {level}",
            )

    return BreakdownFactor(
        factor="buyer",
        label="Buyer relevance",
        points=0,
        max_points=MAX_BUYER,
        detail="No prior buyer relationship detected",
    )


def _has_relevance_signal(factors: list[BreakdownFactor]) -> bool:
    by_id = {f.factor: f for f in factors}
    keywords = by_id.get("keywords")
    category = by_id.get("category")
    specialization = by_id.get("specialization")
    scope = by_id.get("scope")
    location = by_id.get("location")
    return bool(
        (keywords and keywords.points > 0)
        or (category and category.points > 0)
        or (specialization and specialization.points > 0)
        or (scope and scope.points > 0)
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


_EXCLUDED_PROCUREMENT_DETAIL = "Excluded: non-construction procurement"


def _excluded_procurement_match() -> ScoredConstructionMatch:
    """Zero-score result for L1 non-construction procurement titles."""
    labels = {
        "keywords": "Trade keywords",
        "category": "Project type",
        "specialization": "Domain specialization",
        "scope": "Construction scope",
        "location": "Region / service area",
        "value": "Project value range",
        "buyer": "Buyer relevance",
        "reliability": "Profile reliability",
        "freshness": "Tender deadline",
    }
    max_points = {
        "keywords": MAX_KEYWORDS,
        "category": MAX_CATEGORY,
        "specialization": MAX_SPECIALIZATION,
        "scope": MAX_SCOPE,
        "location": MAX_LOCATION,
        "value": MAX_VALUE,
        "buyer": MAX_BUYER,
        "reliability": MAX_RELIABILITY,
        "freshness": MAX_FRESHNESS,
    }
    partial = [
        BreakdownFactor(
            factor=key,
            label=labels[key],
            points=0,
            max_points=max_points[key],
            detail=_EXCLUDED_PROCUREMENT_DETAIL,
        )
        for key in CANONICAL_KEYS
    ]
    factors_by_key = {f.factor: f for f in partial}
    breakdown_json = {f.factor: _factor_to_json(f) for f in partial}
    api_breakdown = to_api_breakdown(factors_by_key, key_order=CANONICAL_KEYS)
    assert_score_equals_breakdown(0, api_breakdown)
    return ScoredConstructionMatch(
        score=0,
        breakdown=partial,
        breakdown_json=breakdown_json,
        api_breakdown=api_breakdown,
        match_reason=_EXCLUDED_PROCUREMENT_DETAIL,
    )


def score_construction_match(
    company: Company,
    tender: ConstructionTender,
    tender_source: str,
) -> ScoredConstructionMatch:
    from pipeline.opportunity_discovery import _is_non_construction_procurement

    if _is_non_construction_procurement(tender.title or ""):
        return _excluded_procurement_match()

    partial = [
        score_keywords(company, tender, tender_source),
        score_category(company, tender, tender_source),
        score_specialization(company, tender, tender_source),
        score_scope_alignment(company, tender, tender_source),
        score_location(company, tender, tender_source),
        score_value_fit(company, tender, tender_source),
        score_buyer_relevance(company, tender, tender_source),
    ]
    partial.append(score_reliability(company, partial))
    partial.append(score_freshness(tender, tender_source))

    factors_by_key = {f.factor: f for f in partial}

    # Maintenance denylist: operating/cleaning/tending contracts suppress all
    # variable-component scores so they cannot rank above real construction work.
    if _is_maintenance_contract(tender):
        for key in ("category", "specialization", "scope", "location", "value", "buyer"):
            f = factors_by_key.get(key)
            if f is not None and f.points != 0:
                f.points = 0
                f.detail += " [suppressed: maintenance contract]"
    else:
        # Work-type gate: zero out region and value boosts for tenders that lack
        # any construction-scope signal in the title.
        cat_f = factors_by_key.get("category")
        spec_f = factors_by_key.get("specialization")
        work_type_ok = (
            (cat_f is not None and cat_f.points > 0)
            or (spec_f is not None and spec_f.points > 0)
            or _has_construction_scope(tender, tender_source)
        )
        if not work_type_ok:
            for key in ("location", "value"):
                f = factors_by_key.get(key)
                if f is not None and f.points > 0:
                    f.points = 0
                    f.detail += " [gated: no construction-scope signal]"

    raw_total = sum(f.points for f in partial)
    total = max(0, min(100, raw_total))
    if total != raw_total:
        # Preserve the breakdown invariant (sum of factor points == total) by
        # absorbing the clamp delta into the region/location factor.
        factors_by_key = {f.factor: f for f in partial}
        location = factors_by_key.get("location")
        if location is not None:
            location.points += total - raw_total

    factors_by_key = {f.factor: f for f in partial}
    breakdown_json = {f.factor: _factor_to_json(f) for f in partial}
    api_breakdown = to_api_breakdown(factors_by_key, key_order=CANONICAL_KEYS)
    assert_score_equals_breakdown(total, api_breakdown)

    return ScoredConstructionMatch(
        score=total,
        breakdown=partial,
        breakdown_json=breakdown_json,
        api_breakdown=api_breakdown,
        match_reason=build_match_reason(partial),
    )
