"""Batch BC construction LinkedIn discovery for validation runs."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from research.linkedin.public_fetch import batch_fetch_public_pages
from research.linkedin.scraper.adapter import scrape_company_urls
from research.linkedin.session import profile_is_initialized, resolve_auth_mode, resolve_session_path
from research.linkedin.url_candidates import build_bc_construction_candidates, write_candidates


def discover_bc_construction_batch(
    *,
    min_count: int = 300,
    max_count: int = 500,
    delay_seconds: float = 1.5,
    session_path: str | None = None,
    use_public_fetch: bool = True,
    use_persistent_batch: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if use_persistent_batch and profile_is_initialized():
        from research.linkedin.batch_runner import run_authenticated_batch

        report = run_authenticated_batch(
            offset=0,
            limit=max_count,
            delay_seconds=max(delay_seconds, 2.0),
            headless=True,
        )
        candidates_payload = build_bc_construction_candidates(min_count=min_count, max_count=max_count)
        write_candidates(candidates_payload)
        artifact = {
            "schema_version": "1.0.0",
            "artifact_type": "linkedin_companies_raw",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "bc_construction_validation_persistent_batch",
            "fetch_method": report.get("auth_mode", "playwright_persistent_profile"),
            "read_only": True,
            "db_writes": False,
            "library": "playwright persistent profile + linkedin_scraper",
            "record_count": report.get("processed", 0),
            "batch_report": report,
            "records": [],
        }
        return artifact, candidates_payload

    candidates_payload = build_bc_construction_candidates(min_count=min_count, max_count=max_count)
    write_candidates(candidates_payload)
    candidates = candidates_payload.get("candidates") or []
    urls = [c["linkedin_company_url"] for c in candidates if c.get("linkedin_company_url")]
    hints_by_url = {c["linkedin_company_url"]: c for c in candidates}

    auth_mode = resolve_auth_mode(prefer_profile=False)
    session_path = session_path or resolve_session_path()
    if auth_mode == "session" and session_path and os.path.isfile(session_path):
        scraped = scrape_company_urls(
            urls,
            session_path=session_path,
            headless=True,
            delay_seconds=max(delay_seconds, 2.0),
        )
        records = [r.to_dict() for r in scraped]
        fetch_method = "playwright_storage_state_fallback"
        library = "joeyism/linkedin_scraper + storageState"
    elif use_public_fetch:
        print(f"[discover] Public fetch of {len(urls)} candidate URLs (no auth)", flush=True)
        scraped = batch_fetch_public_pages(urls, delay_seconds=delay_seconds)
        records = [r.to_dict() for r in scraped]
        fetch_method = "public_unauthenticated"
        library = "requests+beautifulsoup (public page meta)"
    else:
        raise RuntimeError(
            "No authenticated profile/session and public fetch disabled. "
            "Run: python research/linkedin/scripts/login_profile.py"
        )

    for rec in records:
        hint = hints_by_url.get(rec.get("linkedin_company_url") or "")
        if not hint:
            continue
        rec["company_name_guess"] = hint.get("company_name_guess")
        if not rec.get("company_name"):
            rec["company_name"] = hint.get("company_name_guess")
        if not rec.get("website") and hint.get("website_hint"):
            rec["website"] = hint["website_hint"]
        if not rec.get("location") and hint.get("city_hint"):
            rec["location"] = hint["city_hint"]
        rec["candidate_source"] = hint.get("candidate_source")
        rec["trade_hint"] = hint.get("trade_hint")
        rec["city_hint"] = hint.get("city_hint")
        rec["linkedin_page_verified"] = rec.get("scrape_status") == "ok"

    artifact = {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_companies_raw",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "bc_construction_validation",
        "fetch_method": fetch_method,
        "read_only": True,
        "db_writes": False,
        "library": library,
        "record_count": len(records),
        "records": records,
    }
    return artifact, candidates_payload
