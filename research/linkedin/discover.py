"""Step 3 — Discover public LinkedIn company pages and save raw JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import RAW_JSON, SAMPLE_RAW, URLS_FILE
from research.linkedin.scraper.adapter import scrape_company_urls


def load_urls(path: Path | None = None) -> list[str]:
    path = path or URLS_FILE
    if not path.exists():
        return []
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def load_sample_raw(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or SAMPLE_RAW
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return list(payload.get("records") or [])
    return list(payload)


def discover_companies(
    *,
    urls: list[str] | None = None,
    use_sample: bool = False,
    session_path: str | None = None,
    headless: bool = True,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    if use_sample:
        records = load_sample_raw()
        mode = "sample"
    else:
        url_list = urls if urls is not None else load_urls()
        if not url_list:
            raise RuntimeError(
                f"No URLs found. Add one URL per line to {URLS_FILE} or pass --use-sample."
            )
        scraped = scrape_company_urls(
            url_list,
            session_path=session_path,
            headless=headless,
            delay_seconds=delay_seconds,
        )
        records = [r.to_dict() for r in scraped]
        mode = "live_scrape"

    artifact = {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_companies_raw",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "read_only": True,
        "db_writes": False,
        "library": "joeyism/linkedin_scraper (linkedin-scraper)",
        "record_count": len(records),
        "records": records,
    }
    return artifact


def write_raw_artifact(artifact: dict[str, Any], path: Path | None = None) -> Path:
    path = path or RAW_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    return path
