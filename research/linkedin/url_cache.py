"""Persistent LinkedIn company URL resolution cache (research only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import URL_CACHE_JSON

SCHEMA_VERSION = "2.0.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_url_cache(path: Path | None = None) -> dict[str, Any]:
    path = path or URL_CACHE_JSON
    if not path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "linkedin_url_cache",
            "read_only": True,
            "db_writes": False,
            "updated_at": None,
            "entries": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("entries", {})
    return payload


def save_url_cache(payload: dict[str, Any], path: Path | None = None) -> Path:
    path = path or URL_CACHE_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["schema_version"] = SCHEMA_VERSION
    payload["artifact_type"] = "linkedin_url_cache"
    payload["read_only"] = True
    payload["db_writes"] = False
    payload["updated_at"] = _utc_now()
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def get_cache_entry(
    normalized_name_key: str,
    *,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    cache = cache if cache is not None else load_url_cache()
    entry = (cache.get("entries") or {}).get(normalized_name_key)
    return entry if isinstance(entry, dict) else None


def set_cache_entry(
    normalized_name_key: str,
    entry: dict[str, Any],
    *,
    cache: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    cache = cache if cache is not None else load_url_cache(path)
    cache.setdefault("entries", {})
    entry = dict(entry)
    entry["normalized_name_key"] = normalized_name_key
    entry.setdefault("cached_at", _utc_now())
    if entry.get("linkedin_url") and not entry.get("canonical_linkedin_url"):
        entry["canonical_linkedin_url"] = entry["linkedin_url"]
    cache["entries"][normalized_name_key] = entry
    return save_url_cache(cache, path)
