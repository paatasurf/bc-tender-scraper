"""Read-only loader for local Market Registry name keys (no DB, no Registry Engine)."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from research.linkedin.paths import DEFAULT_MARKET_REGISTRY_BASELINE, DEFAULT_ODBUS_CSV, REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402


def load_market_registry_name_keys(
    *,
    baseline_path: Path | None = None,
    odbus_path: Path | None = None,
) -> dict[str, dict[str, str]]:
    """Return normalized_name_key → {display_name, source}."""
    baseline_path = baseline_path or DEFAULT_MARKET_REGISTRY_BASELINE
    odbus_path = odbus_path or DEFAULT_ODBUS_CSV
    index: dict[str, dict[str, str]] = {}

    def _add(name: str, *, source: str) -> None:
        key = normalize_vendor_name(name)
        if key and key not in index:
            index[key] = {"display_name": name.strip(), "source": source}

    if baseline_path.exists():
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        for rec in payload.get("records") or []:
            for field in ("canonical_company_name", "legal_name", "display_name"):
                if rec.get(field):
                    _add(str(rec[field]), source="enterprise_seed_baseline")

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
                for col in ("business_name", "alt_business_name"):
                    val = row.get(col) or ""
                    if val and val not in ("..", ""):
                        _add(val, source="odbus_bc_naics23")

    return index
