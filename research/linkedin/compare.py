"""Step 5 — Compare LinkedIn discoveries against local Market Registry snapshot."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.market_registry_snapshot import load_market_registry_name_keys
from research.linkedin.paths import NORMALIZED_JSON


def _discovery_confidence(rec: dict[str, Any]) -> float:
    score = 0.0
    if rec.get("linkedin_company_url"):
        score += 1.0
    if rec.get("company_name"):
        score += 1.5
    if rec.get("website"):
        score += 2.0
    if rec.get("industry"):
        score += 1.0
    if rec.get("headquarters") or rec.get("location"):
        score += 1.0
    if rec.get("company_size"):
        score += 1.0
    if rec.get("specialties"):
        score += 1.0
    if rec.get("description"):
        score += 1.0
    if rec.get("founded"):
        score += 0.5
    if rec.get("scrape_status") == "ok":
        score += 1.0
    return round(score, 2)


def compare_to_market_registry(
    normalized: dict[str, Any],
    *,
    registry_index: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    registry_index = registry_index or load_market_registry_name_keys()
    already_known: list[dict[str, Any]] = []
    potentially_new: list[dict[str, Any]] = []
    possible_duplicates: list[dict[str, Any]] = []

    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in normalized.get("records") or []:
        key = rec.get("normalized_name_key") or ""
        if key:
            by_key[key].append(rec)

    for key, group in by_key.items():
        if len(group) > 1:
            possible_duplicates.append(
                {
                    "normalized_name_key": key,
                    "count": len(group),
                    "company_names": [g.get("company_name") for g in group],
                    "linkedin_urls": [g.get("linkedin_company_url") for g in group],
                }
            )

    seen_new_keys: set[str] = set()
    for rec in normalized.get("records") or []:
        key = rec.get("normalized_name_key") or ""
        if not key:
            potentially_new.append(
                {
                    **rec,
                    "match_status": "unmatched_no_name_key",
                    "discovery_confidence": _discovery_confidence(rec),
                }
            )
            continue
        if key in registry_index:
            already_known.append(
                {
                    "company_name": rec.get("company_name"),
                    "normalized_name_key": key,
                    "linkedin_company_url": rec.get("linkedin_company_url"),
                    "registry_display_name": registry_index[key]["display_name"],
                    "registry_source": registry_index[key]["source"],
                }
            )
        elif key not in seen_new_keys:
            seen_new_keys.add(key)
            potentially_new.append(
                {
                    **rec,
                    "match_status": "not_in_market_registry_snapshot",
                    "discovery_confidence": _discovery_confidence(rec),
                }
            )

    potentially_new.sort(key=lambda r: (-r.get("discovery_confidence", 0), r.get("company_name") or ""))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_registry_pool_size": len(registry_index),
        "linkedin_record_count": normalized.get("record_count", 0),
        "already_known_count": len(already_known),
        "potentially_new_count": len(potentially_new),
        "possible_duplicates_count": len(possible_duplicates),
        "already_known": already_known,
        "potentially_new": potentially_new,
        "possible_duplicates": possible_duplicates,
    }


def load_normalized(path: Path | None = None) -> dict[str, Any]:
    path = path or NORMALIZED_JSON
    return json.loads(path.read_text(encoding="utf-8"))


def run_compare(*, normalized_path: Path | None = None) -> dict[str, Any]:
    normalized = load_normalized(normalized_path)
    return compare_to_market_registry(normalized)
