"""Build BC construction LinkedIn URL candidates for batch discovery."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import BC_CANDIDATES_JSON, REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402
from research.linkedin.sources_index import _association_members_path, _load_json_records  # noqa: E402
from research.linkedin.paths import ASSOCIATION_SOURCES, DEFAULT_ODBUS_CSV  # noqa: E402

TARGET_TRADES = (
    "general contractor",
    "general contracting",
    "commercial",
    "civil",
    "mechanical",
    "electrical",
    "roofing",
    "concrete",
    "steel",
    "excavation",
    "excavating",
    "hvac",
    "plumbing",
    "rebar",
    "structural",
    "earthwork",
    "grading",
    "paving",
    "demolition",
)

BC_MARKERS = (
    "british columbia",
    " bc",
    "bc,",
    "vancouver",
    "victoria",
    "kelowna",
    "kamloops",
    "prince george",
    "nanaimo",
    "surrey",
    "burnaby",
    "richmond",
)


def slugify_linkedin_company(name: str) -> str:
    cleaned = re.sub(r"\b\d{7,}\s*bc\s*ltd\.?\b", "", name, flags=re.I)
    cleaned = re.sub(r"\bdba\b.*", "", cleaned, flags=re.I)
    cleaned = re.sub(
        r"\b(incorporated|inc|ltd|limited|corp|corporation|llc|lp|co|company|the)\b\.?",
        " ",
        cleaned,
        flags=re.I,
    )
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]


def linkedin_company_url(slug: str) -> str | None:
    slug = (slug or "").strip("-")
    if not slug:
        return None
    return f"https://www.linkedin.com/company/{slug}/"


def _text_blob(rec: dict[str, Any]) -> str:
    parts = [
        rec.get("company_name") or "",
        rec.get("business_category") or "",
        rec.get("contractor_type") or "",
        rec.get("trade_classification") or "",
        rec.get("description") or "",
        rec.get("city") or "",
        " ".join(rec.get("specialties") or []),
    ]
    return " ".join(parts).lower()


def matches_bc_construction_trade(rec: dict[str, Any]) -> bool:
    blob = _text_blob(rec)
    if not any(trade in blob for trade in TARGET_TRADES):
        gc = (rec.get("business_category") or rec.get("contractor_type") or "").lower()
        if "general contractor" in gc or "trade contractor" in gc:
            if any(t in blob for t in ("roof", "concrete", "steel", "excav", "electrical", "mechanical", "civil", "commercial")):
                return True
        return False
    return True


def is_bc_company(rec: dict[str, Any]) -> bool:
    blob = _text_blob(rec)
    if any(marker in blob for marker in BC_MARKERS):
        return True
    city = (rec.get("city") or "").strip()
    return bool(city)


def _candidate_from_name(
    name: str,
    *,
    source: str,
    website: str | None = None,
    city: str | None = None,
    trade_hint: str | None = None,
) -> dict[str, Any] | None:
    name = (name or "").strip()
    if not name or len(name) < 3:
        return None
    slug = slugify_linkedin_company(name)
    if len(slug) < 3:
        return None
    url = linkedin_company_url(slug)
    if not url:
        return None
    return {
        "company_name_guess": name,
        "normalized_name_key": normalize_vendor_name(name),
        "linkedin_company_url": url,
        "linkedin_slug": slug,
        "website_hint": website,
        "city_hint": city,
        "trade_hint": trade_hint,
        "candidate_source": source,
    }


def build_bc_construction_candidates(
    *,
    min_count: int = 300,
    max_count: int = 500,
    prefer_unknown: bool = True,
) -> dict[str, Any]:
    from research.linkedin.sources_index import load_tenderscope_known_index

    known_index = load_tenderscope_known_index() if prefer_unknown else None
    seen_keys: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def _add(candidate: dict[str, Any] | None) -> None:
        if not candidate:
            return
        key = candidate.get("normalized_name_key") or ""
        url = candidate.get("linkedin_company_url") or ""
        if prefer_unknown and key and known_index and known_index.is_known(key):
            return
        dedupe = key or url
        if dedupe in seen_keys:
            return
        seen_keys.add(dedupe)
        candidates.append(candidate)

    if prefer_unknown:
        for source in ASSOCIATION_SOURCES:
            path = _association_members_path(source)
            for rec in _load_json_records(path):
                if not is_bc_company(rec):
                    continue
                if not matches_bc_construction_trade(rec):
                    continue
                trade = rec.get("trade_classification") or rec.get("business_category") or ""
                _add(
                    _candidate_from_name(
                        rec.get("company_name") or "",
                        source=f"association_{source}",
                        website=rec.get("website"),
                        city=rec.get("city"),
                        trade_hint=str(trade),
                    )
                )
    else:
        if DEFAULT_ODBUS_CSV.exists():
            import csv

            with DEFAULT_ODBUS_CSV.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if (row.get("prov_terr") or "").upper() != "BC":
                        continue
                    source_naics = (row.get("source_NAICS_primary") or "").strip()
                    derived_naics = (row.get("derived_NAICS") or "").strip()
                    if not (source_naics.startswith("23") or derived_naics.startswith("23")):
                        continue
                    name = row.get("business_name") or ""
                    blob = f"{name} {row.get('alt_business_name','')} {row.get('derived_NAICS_desc','')}".lower()
                    if not any(t in blob for t in TARGET_TRADES) and not source_naics.startswith("23"):
                        continue
                    _add(
                        _candidate_from_name(
                            name,
                            source="odbus_bc_naics23",
                            city=row.get("city") or row.get("derived_city"),
                            trade_hint=row.get("derived_NAICS_desc") or source_naics,
                        )
                    )
        for source in ASSOCIATION_SOURCES:
            path = _association_members_path(source)
            for rec in _load_json_records(path):
                if not is_bc_company(rec):
                    continue
                if not matches_bc_construction_trade(rec):
                    continue
                trade = rec.get("trade_classification") or rec.get("business_category") or ""
                _add(
                    _candidate_from_name(
                        rec.get("company_name") or "",
                        source=f"association_{source}",
                        website=rec.get("website"),
                        city=rec.get("city"),
                        trade_hint=str(trade),
                    )
                )

    candidates.sort(key=lambda c: (c.get("candidate_source") or "", c.get("company_name_guess") or ""))
    if len(candidates) > max_count:
        candidates = candidates[:max_count]

    unknown_count = len(candidates)
    if prefer_unknown and unknown_count < min_count:
        return build_bc_construction_candidates(
            min_count=min_count,
            max_count=max_count,
            prefer_unknown=False,
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_range": f"{min_count}-{max_count}",
        "prefer_unknown": prefer_unknown,
        "candidate_pool": "association_not_in_tenderscope_snapshots" if prefer_unknown else "full_bc_construction",
        "candidate_count": len(candidates),
        "meets_minimum": len(candidates) >= min_count,
        "candidates": candidates,
    }


def write_candidates(payload: dict[str, Any], path: Path | None = None) -> Path:
    path = path or BC_CANDIDATES_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
