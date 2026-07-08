"""Build unified company pool from local source snapshots (read-only)."""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.linkedin.paths import (
    ASSOCIATION_EXPORTS,
    ASSOCIATION_LAKE_GLOB,
    ASSOCIATION_SOURCES,
    DEFAULT_ENTERPRISE_SEED,
    DEFAULT_MARKET_REGISTRY_BASELINE,
    DEFAULT_ODBUS_CSV,
    REPO_ROOT,
)
from research.linkedin.url_candidates import linkedin_company_url, slugify_linkedin_company

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("records") or [])


def _association_path(source: str) -> Path:
    lake = REPO_ROOT / ASSOCIATION_LAKE_GLOB.format(source=source)
    if lake.exists():
        return lake
    return REPO_ROOT / ASSOCIATION_EXPORTS.format(source=source)


@dataclass
class PoolCompany:
    company_name: str
    normalized_name_key: str
    provenance_sources: set[str] = field(default_factory=set)
    source_website: str | None = None
    source_city: str | None = None
    source_industry: str | None = None
    source_specialties: str | None = None
    source_trade_hint: str | None = None
    linkedin_url_candidate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_name": self.company_name,
            "normalized_name_key": self.normalized_name_key,
            "provenance_sources": sorted(self.provenance_sources),
            "source_website": self.source_website,
            "source_city": self.source_city,
            "source_industry": self.source_industry,
            "source_specialties": self.source_specialties,
            "source_trade_hint": self.source_trade_hint,
            "linkedin_url_candidate": self.linkedin_url_candidate,
        }


def _get_or_create(pool: dict[str, PoolCompany], name: str, source: str) -> PoolCompany | None:
    name = (name or "").strip()
    key = normalize_vendor_name(name)
    if not key:
        return None
    if key not in pool:
        slug_url = linkedin_company_url(slugify_linkedin_company(name))
        pool[key] = PoolCompany(
            company_name=name,
            normalized_name_key=key,
            linkedin_url_candidate=slug_url or "",
        )
    row = pool[key]
    row.provenance_sources.add(source)
    if len(name) > len(row.company_name):
        row.company_name = name
    return row


def build_source_pool(*, bc_construction_only: bool = True) -> dict[str, PoolCompany]:
    """Union of companies from Enterprise Seed, ODB, associations, and MR baseline."""
    pool: dict[str, PoolCompany] = {}

    for rec in _load_json_records(DEFAULT_ENTERPRISE_SEED):
        for field_name in ("canonical_company_name", "legal_name", "display_name"):
            if rec.get(field_name):
                row = _get_or_create(pool, str(rec[field_name]), "enterprise_seed")
                if row and not row.source_website and rec.get("website"):
                    row.source_website = str(rec["website"])

    for rec in _load_json_records(DEFAULT_MARKET_REGISTRY_BASELINE):
        for field_name in ("canonical_company_name", "legal_name", "display_name"):
            if rec.get(field_name):
                _get_or_create(pool, str(rec[field_name]), "market_registry_baseline")

    if DEFAULT_ODBUS_CSV.exists():
        with DEFAULT_ODBUS_CSV.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if (row.get("prov_terr") or "").upper() != "BC":
                    continue
                source_naics = (row.get("source_NAICS_primary") or "").strip()
                derived_naics = (row.get("derived_NAICS") or "").strip()
                if bc_construction_only and not (
                    source_naics.startswith("23") or derived_naics.startswith("23")
                ):
                    continue
                for col in ("business_name", "alt_business_name"):
                    val = row.get(col) or ""
                    if val and val not in ("..", ""):
                        rec = _get_or_create(pool, val, "odbus_bc_naics23")
                        if rec:
                            if not rec.source_city:
                                rec.source_city = row.get("city") or row.get("derived_city")
                            if not rec.source_industry:
                                rec.source_industry = row.get("derived_NAICS_desc") or source_naics

    for source in ASSOCIATION_SOURCES:
        label = f"association_{source}"
        for rec in _load_json_records(_association_path(source)):
            name = rec.get("company_name") or ""
            row = _get_or_create(pool, name, label)
            if not row:
                continue
            if not row.source_website and rec.get("website"):
                row.source_website = str(rec["website"])
            if not row.source_city and rec.get("city"):
                row.source_city = str(rec["city"])
            specs = rec.get("specialties") or []
            if specs and not row.source_specialties:
                row.source_specialties = ", ".join(specs) if isinstance(specs, list) else str(specs)
            trade = rec.get("trade_classification") or rec.get("business_category") or rec.get("contractor_type")
            if trade and not row.source_trade_hint:
                row.source_trade_hint = str(trade)

    return pool


def pool_to_list(pool: dict[str, PoolCompany]) -> list[dict[str, Any]]:
    return [row.to_dict() for row in sorted(pool.values(), key=lambda r: r.normalized_name_key)]
