"""Load all local comparison sources (read-only, no DB)."""

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

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402


@dataclass
class SourceIndex:
    """Normalized name key → set of source labels."""

    keys: dict[str, set[str]] = field(default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, source: str) -> None:
        key = normalize_vendor_name(name)
        if not key:
            return
        self.keys.setdefault(key, set()).add(source)
        if key not in self.display_names:
            self.display_names[key] = name.strip()

    def sources_for(self, key: str) -> list[str]:
        return sorted(self.keys.get(key) or [])

    def is_known(self, key: str) -> bool:
        return bool(key and key in self.keys)


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("records") or [])


def _association_members_path(source: str) -> Path:
    lake = REPO_ROOT / ASSOCIATION_LAKE_GLOB.format(source=source)
    if lake.exists():
        return lake
    return REPO_ROOT / ASSOCIATION_EXPORTS.format(source=source)


def load_enterprise_seed_index(seed_path: Path | None = None) -> SourceIndex:
    seed_path = seed_path or DEFAULT_ENTERPRISE_SEED
    idx = SourceIndex()
    for rec in _load_json_records(seed_path):
        for field_name in ("canonical_company_name", "legal_name", "display_name"):
            if rec.get(field_name):
                idx.add(str(rec[field_name]), "enterprise_seed")
    idx.stats["enterprise_seed_records"] = len(_load_json_records(seed_path))
    idx.stats["enterprise_seed_keys"] = sum(1 for s in idx.keys.values() if "enterprise_seed" in s)
    return idx


def load_odbus_index(odbus_path: Path | None = None) -> SourceIndex:
    odbus_path = odbus_path or DEFAULT_ODBUS_CSV
    idx = SourceIndex()
    count = 0
    if odbus_path.exists():
        with odbus_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if (row.get("prov_terr") or "").upper() != "BC":
                    continue
                source_naics = (row.get("source_NAICS_primary") or "").strip()
                derived_naics = (row.get("derived_NAICS") or "").strip()
                if not (source_naics.startswith("23") or derived_naics.startswith("23")):
                    continue
                count += 1
                for col in ("business_name", "alt_business_name"):
                    val = row.get(col) or ""
                    if val and val not in ("..", ""):
                        idx.add(val, "odbus_bc_naics23")
    idx.stats["odbus_bc_naics23_rows"] = count
    idx.stats["odbus_keys"] = sum(1 for s in idx.keys.values() if "odbus_bc_naics23" in s)
    return idx


def load_association_index() -> SourceIndex:
    idx = SourceIndex()
    total = 0
    for source in ASSOCIATION_SOURCES:
        path = _association_members_path(source)
        records = _load_json_records(path)
        total += len(records)
        label = f"association_{source}"
        for rec in records:
            name = rec.get("company_name") or ""
            if name:
                idx.add(name, label)
        idx.stats[f"{label}_records"] = len(records)
    idx.stats["association_total_records"] = total
    idx.stats["association_keys"] = sum(
        1 for sources in idx.keys.values() if any(s.startswith("association_") for s in sources)
    )
    return idx


def load_market_registry_baseline_index(baseline_path: Path | None = None) -> SourceIndex:
    baseline_path = baseline_path or DEFAULT_MARKET_REGISTRY_BASELINE
    idx = SourceIndex()
    for rec in _load_json_records(baseline_path):
        for field_name in ("canonical_company_name", "legal_name", "display_name"):
            if rec.get(field_name):
                idx.add(str(rec[field_name]), "market_registry_baseline")
    idx.stats["market_registry_baseline_records"] = len(_load_json_records(baseline_path))
    idx.stats["market_registry_baseline_keys"] = sum(
        1 for s in idx.keys.values() if "market_registry_baseline" in s
    )
    return idx


def load_tenderscope_known_index() -> SourceIndex:
    """Enterprise Seed + ODB + Market Registry baseline (excludes association-only coverage)."""
    combined = SourceIndex()
    for loader in (
        load_enterprise_seed_index,
        load_odbus_index,
        load_market_registry_baseline_index,
    ):
        part = loader()
        combined.stats.update(part.stats)
        for key, sources in part.keys.items():
            combined.keys.setdefault(key, set()).update(sources)
            if key not in combined.display_names and key in part.display_names:
                combined.display_names[key] = part.display_names[key]
    combined.stats["tenderscope_known_unique_keys"] = len(combined.keys)
    return combined


def load_combined_source_index() -> SourceIndex:
    combined = SourceIndex()
    for loader in (
        load_enterprise_seed_index,
        load_odbus_index,
        load_association_index,
        load_market_registry_baseline_index,
    ):
        part = loader()
        combined.stats.update(part.stats)
        for key, sources in part.keys.items():
            combined.keys.setdefault(key, set()).update(sources)
            if key not in combined.display_names and key in part.display_names:
                combined.display_names[key] = part.display_names[key]
    combined.stats["combined_unique_keys"] = len(combined.keys)
    return combined
