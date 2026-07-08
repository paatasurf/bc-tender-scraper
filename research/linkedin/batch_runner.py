"""Resumable authenticated LinkedIn batch runner with per-company cache."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from research.linkedin.batch_report import count_enrichment_fields, write_batch_reports
from research.linkedin.company_cache import cache_exists, load_cached_company, save_cached_company
from research.linkedin.paths import RAW_JSON
from research.linkedin.progress_tracker import (
    init_progress,
    load_progress,
    mark_finished,
    record_processed,
    resolve_offset,
    save_progress,
)
from research.linkedin.scraper.adapter import LinkedInCompanyRecord, scrape_company_urls
from research.linkedin.scraper.persistent_browser import (
    persistent_browser_context,
    scrape_single_company,
)
from research.linkedin.session import (
    ProfileExpiredError,
    SessionExpiredError,
    print_profile_refresh_message,
    print_session_refresh_message,
    profile_is_initialized,
    resolve_auth_mode,
    resolve_session_path,
)
from research.linkedin.source_pool import PoolCompany, build_source_pool
from research.linkedin.url_resolver import get_enrichable_url, is_numbered_bc_shell

MAX_RETRIES = 2
TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "network",
    "connection",
    "429",
    "502",
    "503",
    "504",
    "temporarily",
    "target closed",
)
PERMANENT_MARKERS = (
    "404",
    "not found",
    "page not found",
    "invalid url",
    "no company",
    "could not find",
    "no module named",
    "invalid linkedin url",
    "empty linkedin slug",
)


@dataclass
class QueueItem:
    company_name: str
    normalized_name_key: str
    linkedin_url: str
    provenance_sources: list[str]
    source_website: str | None = None
    source_city: str | None = None
    url_confidence: int = 0
    url_match_method: str = ""


def build_queue(
    *,
    bc_construction_only: bool = True,
    require_resolved_url: bool = True,
) -> list[QueueItem]:
    pool = build_source_pool(bc_construction_only=bc_construction_only)
    items: list[QueueItem] = []
    for row in pool.values():
        if is_numbered_bc_shell(row.company_name):
            continue
        resolved = get_enrichable_url(row.normalized_name_key)
        if require_resolved_url:
            if not resolved or not resolved.linkedin_url:
                continue
            linkedin_url = resolved.linkedin_url
            confidence = resolved.url_confidence
            method = resolved.match_method
        elif resolved and resolved.linkedin_url:
            linkedin_url = resolved.linkedin_url
            confidence = resolved.url_confidence
            method = resolved.match_method
        else:
            linkedin_url = row.linkedin_url_candidate
            confidence = 0
            method = "legacy_slug_candidate"
        if not linkedin_url or _invalid_linkedin_url(linkedin_url):
            continue
        items.append(
            QueueItem(
                company_name=row.company_name,
                normalized_name_key=row.normalized_name_key,
                linkedin_url=linkedin_url,
                provenance_sources=sorted(row.provenance_sources),
                source_website=row.source_website,
                source_city=row.source_city,
                url_confidence=confidence,
                url_match_method=method,
            )
        )
    return sorted(items, key=lambda item: (-item.url_confidence, item.normalized_name_key))


def ensure_scraper_dependencies() -> None:
    """Fail fast if optional LinkedIn scraper deps are missing."""
    try:
        import linkedin_scraper  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing linkedin-scraper dependency. Install with:\n"
            "  pip install -r research/linkedin/requirements.txt\n"
            "  playwright install chromium"
        ) from exc
    try:
        import playwright  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing playwright dependency. Install with:\n"
            "  pip install -r research/linkedin/requirements.txt\n"
            "  playwright install chromium"
        ) from exc


def _invalid_linkedin_url(url: str) -> bool:
    return not url or url.rstrip("/").endswith("/company")


def _is_transient(error: str) -> bool:
    lower = error.lower()
    return any(marker in lower for marker in TRANSIENT_MARKERS)


def _is_permanent(error: str) -> bool:
    lower = error.lower()
    return any(marker in lower for marker in PERMANENT_MARKERS)


def _record_to_cache_dict(
    item: QueueItem,
    record: LinkedInCompanyRecord,
    *,
    from_cache: bool = False,
) -> dict[str, Any]:
    data = record.to_dict()
    data["company_name_guess"] = item.company_name
    data["normalized_name_key"] = item.normalized_name_key
    data["provenance_sources"] = item.provenance_sources
    data["source_website"] = item.source_website
    data["source_city"] = item.source_city
    data["linkedin_page_verified"] = record.scrape_status == "ok"
    data["from_cache"] = from_cache
    return data


def _error_record(item: QueueItem, error: str, *, permanent: bool, stage: str = "unknown") -> LinkedInCompanyRecord:
    return LinkedInCompanyRecord(
        company_name=item.company_name,
        linkedin_company_url=item.linkedin_url,
        scrape_status="error",
        scrape_error=error[:500],
        scraped_at=datetime.now(timezone.utc).isoformat(),
        source_fields={
            "fetch_mode": "playwright_persistent_profile",
            "permanent_failure": permanent,
            "failure_stage": stage,
        },
    )


async def _scrape_with_retries(page: Any, item: QueueItem) -> LinkedInCompanyRecord:
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            return await scrape_single_company(page, item.linkedin_url, company_name=item.company_name)
        except ProfileExpiredError:
            raise
        except ModuleNotFoundError as exc:
            return _error_record(item, str(exc), permanent=True, stage="dependency")
        except Exception as exc:
            last_error = str(exc)
            stage = "parsing" if "linkedin_scraper" in last_error.lower() else "browser_navigation"
            if _is_permanent(last_error):
                return _error_record(item, last_error, permanent=True, stage=stage)
            if attempt <= MAX_RETRIES and _is_transient(last_error):
                print(f"[batch]   retry {attempt}/{MAX_RETRIES}: {last_error[:120]}", flush=True)
                await asyncio.sleep(2.0 * attempt)
                continue
            return _error_record(
                item,
                last_error,
                permanent=_is_permanent(last_error),
                stage=stage,
            )
    return _error_record(item, last_error or "unknown error", permanent=True, stage="browser_navigation")


async def _run_profile_batch(
    batch: list[QueueItem],
    *,
    refresh: bool,
    delay_seconds: float,
    headless: bool,
    progress: dict[str, Any],
    batch_start_offset: int,
) -> dict[str, Any]:
    stats = {
        "auth_mode": "playwright_persistent_profile",
        "processed": 0,
        "cached_skipped": 0,
        "scraped_ok": 0,
        "failed_permanent": 0,
        "failed_transient": 0,
        "new_linkedin_pages": 0,
        "errors": [],
        "batch_records": [],
    }

    async with persistent_browser_context(headless=headless) as page:
        for index, item in enumerate(batch):
            absolute_index = batch_start_offset + index
            stats["processed"] += 1

            if cache_exists(item.company_name) and not refresh:
                cached = load_cached_company(item.company_name)
                record_dict = (cached or {}).get("record") or {}
                stats["cached_skipped"] += 1
                print(
                    f"[batch] {index + 1}/{len(batch)} CACHED {item.company_name} "
                    f"status={record_dict.get('scrape_status')}",
                    flush=True,
                )
                if record_dict.get("scrape_status") == "ok":
                    stats["new_linkedin_pages"] += 0
                record_processed(
                    progress,
                    company_name=item.company_name,
                    normalized_name_key=item.normalized_name_key,
                    linkedin_url=item.linkedin_url,
                    status="cached",
                    batch_offset=absolute_index,
                )
                stats["batch_records"].append(record_dict)
                continue

            print(
                f"[batch] {index + 1}/{len(batch)} SCRAPE {item.company_name} url={item.linkedin_url}",
                flush=True,
            )
            if _invalid_linkedin_url(item.linkedin_url):
                record = _error_record(
                    item,
                    "Invalid LinkedIn URL (empty slug)",
                    permanent=True,
                    stage="url_resolution",
                )
            else:
                try:
                    record = await _scrape_with_retries(page, item)
                except ProfileExpiredError:
                    print_profile_refresh_message()
                    save_progress(progress)
                    raise

            record_dict = _record_to_cache_dict(item, record)
            save_cached_company(item.company_name, record_dict)
            stats["batch_records"].append(record_dict)

            if record.scrape_status == "ok":
                stats["scraped_ok"] += 1
                stats["new_linkedin_pages"] += 1
                status = "ok"
                error = None
                permanent = False
            else:
                error = record.scrape_error or "unknown error"
                permanent = _is_permanent(error)
                if permanent:
                    stats["failed_permanent"] += 1
                else:
                    stats["failed_transient"] += 1
                stats["errors"].append(
                    {"company_name": item.company_name, "error": error, "permanent": permanent}
                )
                status = "failed"

            record_processed(
                progress,
                company_name=item.company_name,
                normalized_name_key=item.normalized_name_key,
                linkedin_url=item.linkedin_url,
                status=status,
                error=error,
                permanent=permanent,
                batch_offset=absolute_index,
            )

            if index < len(batch) - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    return stats


def _run_session_batch(
    batch: list[QueueItem],
    *,
    refresh: bool,
    delay_seconds: float,
    headless: bool,
    progress: dict[str, Any],
    batch_start_offset: int,
    session_path: str | None,
) -> dict[str, Any]:
    stats = {
        "auth_mode": "playwright_storage_state_fallback",
        "processed": 0,
        "cached_skipped": 0,
        "scraped_ok": 0,
        "failed_permanent": 0,
        "failed_transient": 0,
        "new_linkedin_pages": 0,
        "errors": [],
        "batch_records": [],
    }

    to_scrape: list[tuple[int, QueueItem]] = []
    for index, item in enumerate(batch):
        absolute_index = batch_start_offset + index
        stats["processed"] += 1
        if cache_exists(item.company_name) and not refresh:
            cached = load_cached_company(item.company_name)
            record_dict = (cached or {}).get("record") or {}
            stats["cached_skipped"] += 1
            stats["batch_records"].append(record_dict)
            record_processed(
                progress,
                company_name=item.company_name,
                normalized_name_key=item.normalized_name_key,
                linkedin_url=item.linkedin_url,
                status="cached",
                batch_offset=absolute_index,
            )
            continue
        to_scrape.append((absolute_index, item))

    if to_scrape:
        try:
            scraped = scrape_company_urls(
                [item.linkedin_url for _, item in to_scrape],
                session_path=session_path,
                headless=headless,
                delay_seconds=max(delay_seconds, 2.0),
            )
        except SessionExpiredError:
            print_session_refresh_message()
            save_progress(progress)
            raise

        for (absolute_index, item), record in zip(to_scrape, scraped, strict=False):
            record_dict = _record_to_cache_dict(item, record)
            save_cached_company(item.company_name, record_dict)
            stats["batch_records"].append(record_dict)

            if record.scrape_status == "ok":
                stats["scraped_ok"] += 1
                stats["new_linkedin_pages"] += 1
                status = "ok"
                error = None
                permanent = False
            else:
                error = record.scrape_error or "unknown error"
                permanent = _is_permanent(error)
                if permanent:
                    stats["failed_permanent"] += 1
                else:
                    stats["failed_transient"] += 1
                stats["errors"].append(
                    {"company_name": item.company_name, "error": error, "permanent": permanent}
                )
                status = "failed"

            record_processed(
                progress,
                company_name=item.company_name,
                normalized_name_key=item.normalized_name_key,
                linkedin_url=item.linkedin_url,
                status=status,
                error=error,
                permanent=permanent,
                batch_offset=absolute_index,
            )

    return stats


def merge_cache_to_raw_artifact() -> None:
    from research.linkedin.company_cache import CACHE_DIR

    records: list[dict[str, Any]] = []
    if CACHE_DIR.exists():
        for path in sorted(CACHE_DIR.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = payload.get("record")
            if record:
                records.append(record)

    artifact = {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_companies_raw",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "authenticated_batch_cache_merge",
        "fetch_method": "playwright_persistent_profile",
        "read_only": True,
        "db_writes": False,
        "record_count": len(records),
        "records": records,
    }
    RAW_JSON.parent.mkdir(parents=True, exist_ok=True)
    RAW_JSON.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")


def run_authenticated_batch(
    *,
    offset: int | None = None,
    limit: int = 50,
    refresh: bool = False,
    delay_seconds: float = 2.0,
    headless: bool = True,
    force_session: bool = False,
    bc_construction_only: bool = True,
) -> dict[str, Any]:
    queue = build_queue(bc_construction_only=bc_construction_only)
    progress = init_progress(total_queue=len(queue))
    start_offset = resolve_offset(offset, progress)
    batch = queue[start_offset : start_offset + limit]

    if not batch:
        mark_finished(progress)
        return {
            "message": "Queue complete — nothing left to process.",
            "next_offset": start_offset,
            "remaining": 0,
        }

    auth_mode = resolve_auth_mode(prefer_profile=True, force_session=force_session)
    if auth_mode == "public":
        raise RuntimeError(
            "No authenticated profile or session found. "
            "Run: python research/linkedin/scripts/login_profile.py"
        )

    ensure_scraper_dependencies()

    started_at = datetime.now(timezone.utc).isoformat()

    if auth_mode == "profile":
        if not profile_is_initialized():
            print_profile_refresh_message()
            raise ProfileExpiredError("Browser profile not initialized.")
        stats = asyncio.run(
            _run_profile_batch(
                batch,
                refresh=refresh,
                delay_seconds=delay_seconds,
                headless=headless,
                progress=progress,
                batch_start_offset=start_offset,
            )
        )
    else:
        stats = _run_session_batch(
            batch,
            refresh=refresh,
            delay_seconds=delay_seconds,
            headless=headless,
            progress=progress,
            batch_start_offset=start_offset,
            session_path=resolve_session_path(),
        )

    merge_cache_to_raw_artifact()

    next_offset = start_offset + len(batch)
    remaining = max(0, len(queue) - next_offset)
    if remaining == 0:
        mark_finished(progress)

    report_stats = {
        **stats,
        "offset": start_offset,
        "limit": limit,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "next_offset": next_offset,
        "remaining": remaining,
        "total_queue": len(queue),
        "enrichment_fields_added": count_enrichment_fields(stats.get("batch_records") or []),
    }
    report, _md = write_batch_reports(report_stats)
    return report
