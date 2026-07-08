"""Per-company JSON cache for incremental LinkedIn scraping."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import CACHE_DIR

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_cache_stem(company_name: str) -> str:
    name = (company_name or "unknown").strip()
    name = INVALID_FILENAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "unknown"


def cache_path(company_name: str) -> Path:
    return CACHE_DIR / f"{sanitize_cache_stem(company_name)}.json"


def cache_exists(company_name: str) -> bool:
    return cache_path(company_name).is_file()


def load_cached_company(company_name: str) -> dict[str, Any] | None:
    path = cache_path(company_name)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cached_company(company_name: str, record: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(company_name)
    payload = {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_company_cache_entry",
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "company_name": company_name,
        "read_only": True,
        "db_writes": False,
        "record": record,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def list_cached_companies() -> list[str]:
    if not CACHE_DIR.exists():
        return []
    return sorted(path.stem for path in CACHE_DIR.glob("*.json"))
