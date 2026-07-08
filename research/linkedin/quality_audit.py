"""Quality audit for top new LinkedIn discoveries."""

from __future__ import annotations

import re
from typing import Any

CONSTRUCTION_TERMS = (
    "construction",
    "contractor",
    "builder",
    "building",
    "general contractor",
    "civil",
    "excavat",
    "concrete",
    "roof",
    "steel",
    "mechanical",
    "electrical",
    "hvac",
    "plumb",
    "rebar",
    "earthwork",
    "grading",
    "paving",
    "demolition",
    "infrastructure",
    "industrial",
)

COMMERCIAL_TERMS = ("commercial", "ici", "industrial", "institutional", "tenant improvement", "office", "retail")
RESIDENTIAL_TERMS = ("residential", "single family", "multi-family", "multifamily", "home builder", "custom home")
CONSULTANT_TERMS = (
    "consulting",
    "consultant",
    "engineering",
    "architect",
    "design",
    "survey",
    "planning",
    "advisory",
)
SUPPLIER_TERMS = (
    "supplier",
    "supply",
    "distributor",
    "manufacturer",
    "rental",
    "wholesale",
    "materials",
    "equipment sales",
)


def _blob(rec: dict[str, Any]) -> str:
    parts = [
        rec.get("company_name") or "",
        rec.get("industry") or "",
        rec.get("description") or "",
        rec.get("specialties") or "",
        rec.get("trade_hint") or "",
    ]
    return " ".join(parts).lower()


def _has_any(blob: str, terms: tuple[str, ...]) -> bool:
    return any(term in blob for term in terms)


def classify_company_type(rec: dict[str, Any]) -> dict[str, Any]:
    blob = _blob(rec)
    construction = _has_any(blob, CONSTRUCTION_TERMS)
    commercial = _has_any(blob, COMMERCIAL_TERMS)
    residential = _has_any(blob, RESIDENTIAL_TERMS)
    consultant = _has_any(blob, CONSULTANT_TERMS) and not construction
    supplier = _has_any(blob, SUPPLIER_TERMS) and not construction

    if consultant:
        primary = "consultant"
    elif supplier:
        primary = "supplier"
    elif construction and commercial and residential:
        primary = "construction_mixed"
    elif construction and commercial:
        primary = "commercial_contractor"
    elif construction and residential:
        primary = "residential_contractor"
    elif construction:
        primary = "construction"
    elif commercial:
        primary = "commercial"
    elif residential:
        primary = "residential"
    else:
        primary = "unknown"

    return {
        "construction_related": construction,
        "commercial": commercial,
        "residential": residential,
        "consultant": consultant,
        "supplier": supplier,
        "primary_classification": primary,
        "likely_false_positive": primary in ("consultant", "supplier", "unknown") and not construction,
    }


def audit_top_new_companies(records: list[dict[str, Any]]) -> dict[str, Any]:
    audited: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    false_positives = 0
    for rec in records:
        audit = classify_company_type(rec)
        merged = {**rec, **audit}
        audited.append(merged)
        primary = audit["primary_classification"]
        counts[primary] = counts.get(primary, 0) + 1
        if audit["likely_false_positive"]:
            false_positives += 1
    total = len(records) or 1
    return {
        "audited_count": len(records),
        "classification_counts": counts,
        "likely_false_positives": false_positives,
        "estimated_false_positive_rate_pct": round(false_positives / total * 100, 1),
        "records": audited,
    }


def is_bc_headquarters(rec: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            rec.get("headquarters") or "",
            rec.get("location") or "",
            rec.get("city_hint") or "",
        ]
    ).lower()
    if "british columbia" in blob or re.search(r"\bbc\b", blob):
        return True
    bc_cities = (
        "vancouver",
        "victoria",
        "kelowna",
        "kamloops",
        "prince george",
        "nanaimo",
        "surrey",
        "burnaby",
        "richmond",
        "abbotsford",
        "vernon",
        "penticton",
    )
    return any(city in blob for city in bc_cities)
