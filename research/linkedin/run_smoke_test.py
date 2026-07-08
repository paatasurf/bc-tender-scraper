#!/usr/bin/env python3
"""Authenticated smoke test — 5 confirmed LinkedIn company pages (research only)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.batch_runner import ensure_scraper_dependencies  # noqa: E402
from research.linkedin.paths import RESEARCH_ROOT  # noqa: E402
from research.linkedin.scraper.adapter import LinkedInCompanyRecord  # noqa: E402
from research.linkedin.scraper.persistent_browser import (  # noqa: E402
    persistent_browser_context,
    scrape_single_company,
)
from research.linkedin.session import (  # noqa: E402
    ProfileExpiredError,
    is_login_url,
    print_profile_refresh_message,
    profile_is_initialized,
)

SMOKE_TEST_JSON = RESEARCH_ROOT / "smoke_test_report.json"
SMOKE_TEST_MD = RESEARCH_ROOT / "smoke_test_report.md"

# Confirmed vanity URLs — verified by navigating to each page in an authenticated
# session (July 2026). Older slug guesses in companies_enriched.json redirect to
# /company/unavailable/ or the wrong entity; these are the live official pages.
CONFIRMED_COMPANIES: list[dict[str, Any]] = [
    {
        "company_name": "Houle Electric",
        "linkedin_url": "https://www.linkedin.com/company/houle-electric-limited/",
        "url_source": "LinkedIn official page (houle.ca / Burnaby BC contractor)",
        "name_tokens": ("houle",),
        "deprecated_slug": "houle-electric",
    },
    {
        "company_name": "Bird Construction",
        "linkedin_url": "https://www.linkedin.com/company/bird-construction-inc/",
        "url_source": "LinkedIn official page (bird.ca / BDT.TO)",
        "name_tokens": ("bird", "construction"),
        "deprecated_slug": "bird-construction",
    },
    {
        "company_name": "Chandos Construction",
        "linkedin_url": "https://www.linkedin.com/company/chandos/",
        "url_source": "LinkedIn official page (chandos.com / Edmonton HQ)",
        "name_tokens": ("chandos",),
        "deprecated_slug": "chandos-construction",
    },
    {
        "company_name": "Ainsworth",
        "linkedin_url": "https://www.linkedin.com/company/ainsworth-inc/",
        "url_source": "LinkedIn official page (ainsworth.com / facilities services)",
        "name_tokens": ("ainsworth",),
        "deprecated_slug": "ainsworth",
    },
    {
        "company_name": "EllisDon",
        "linkedin_url": "https://www.linkedin.com/company/ellisdon/",
        "url_source": "LinkedIn official page (ellisdon.com)",
        "name_tokens": ("ellisdon",),
        "deprecated_slug": None,
    },
]

ENRICHMENT_FIELDS = (
    "company_name",
    "industry",
    "headquarters",
    "company_size",
    "website",
    "description",
    "specialties",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _record_fields(record: LinkedInCompanyRecord | None) -> dict[str, Any]:
    if not record:
        return {field: None for field in ENRICHMENT_FIELDS}
    return {
        "company_name": record.company_name,
        "industry": record.industry,
        "headquarters": record.headquarters,
        "company_size": record.company_size,
        "website": record.website,
        "description": record.description,
        "specialties": record.specialties,
    }


def _enrichment_field_count(fields: dict[str, Any]) -> int:
    return sum(1 for key in ENRICHMENT_FIELDS if _populated(fields.get(key)))


def _is_success(fields: dict[str, Any], *, scrape_status: str) -> bool:
    """scrape ok + company name + at least one other structured field."""
    if scrape_status != "ok" or not _populated(fields.get("company_name")):
        return False
    others = sum(1 for key in ENRICHMENT_FIELDS if key != "company_name" and _populated(fields.get(key)))
    return others >= 1


async def _verify_company_page(page: Any, item: dict[str, Any]) -> dict[str, Any]:
    url = item["linkedin_url"]
    result: dict[str, Any] = {
        "linkedin_url": url,
        "navigation_success": False,
        "final_url": None,
        "http_status": None,
        "page_title": None,
        "verified_official_page": False,
        "verification_notes": [],
    }

    print(f"\n{'=' * 72}", flush=True)
    print(f"COMPANY: {item['company_name']}", flush=True)
    print(f"LinkedIn URL: {url}", flush=True)
    print(f"URL source: {item['url_source']}", flush=True)

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        result["final_url"] = page.url
        result["http_status"] = response.status if response else None
        result["page_title"] = await page.title()

        print(f"Navigation: status={result['http_status']} final_url={result['final_url']}", flush=True)
        print(f"Page title: {result['page_title']}", flush=True)

        if is_login_url(page.url):
            raise ProfileExpiredError("LinkedIn redirected to login during page verification.")

        if "/company/unavailable" in page.url.lower():
            note = "LinkedIn redirected to /company/unavailable/ — slug wrong or page blocked"
            if item.get("deprecated_slug"):
                note += f" (deprecated slug: {item['deprecated_slug']})"
            result["verification_notes"].append(note)
            print(f"VERIFY FAIL: unavailable page — {page.url}", flush=True)
            return result

        if "/company/" not in page.url.lower():
            result["verification_notes"].append("Final URL is not a /company/ page.")
            print(f"VERIFY FAIL: not a company page — {page.url}", flush=True)
            return result

        if result["http_status"] and result["http_status"] >= 400:
            result["verification_notes"].append(f"HTTP {result['http_status']}")
            print(f"VERIFY FAIL: HTTP {result['http_status']}", flush=True)
            return result

        result["navigation_success"] = True

        title_lower = (result["page_title"] or "").lower()
        og_title = await page.evaluate(
            """() => {
                const tag = document.querySelector('meta[property="og:title"]');
                return tag ? tag.getAttribute('content') : null;
            }"""
        )
        og_lower = (og_title or "").lower()
        blob = f"{title_lower} {og_lower}"
        tokens = item["name_tokens"]
        matched = [t for t in tokens if t in blob]
        expected_slug = url.rstrip("/").split("/")[-1]
        slug_ok = expected_slug and expected_slug in page.url.lower()
        if matched:
            result["verified_official_page"] = True
            result["verification_notes"].append(f"Name tokens matched: {matched}")
            print(f"VERIFY OK: official company page (tokens matched: {matched})", flush=True)
        elif slug_ok:
            result["verified_official_page"] = True
            result["verification_notes"].append(f"URL slug confirmed: {expected_slug}")
            print(f"VERIFY OK: URL slug matches ({expected_slug})", flush=True)
        else:
            result["verification_notes"].append(
                f"Name tokens {tokens} not found in title; slug {expected_slug} not in final URL"
            )
            print("VERIFY FAIL: could not confirm official page", flush=True)

        return result
    except ProfileExpiredError:
        raise
    except Exception as exc:
        result["verification_notes"].append(str(exc))
        print(f"VERIFY ERROR: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return result


async def _scrape_one(page: Any, item: dict[str, Any]) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "company_name": item["company_name"],
        "linkedin_url": item["linkedin_url"],
        "url_source": item["url_source"],
        "verification": {},
        "scrape_status": "pending",
        "scrape_error": None,
        "exception_type": None,
        "exception_traceback": None,
        "parsed_fields": {},
        "enrichment_field_count": 0,
        "success": False,
    }

    try:
        outcome["verification"] = await _verify_company_page(page, item)
        if not outcome["verification"].get("navigation_success"):
            outcome["scrape_status"] = "error"
            outcome["scrape_error"] = "Page verification failed before scrape"
            print(f"SCRAPE SKIP: verification failed", flush=True)
            return outcome

        if not outcome["verification"].get("verified_official_page"):
            outcome["scrape_status"] = "error"
            outcome["scrape_error"] = "Could not verify official LinkedIn company page"
            print(f"SCRAPE SKIP: could not verify official page", flush=True)
            return outcome

        print(f"SCRAPE: calling CompanyScraper...", flush=True)
        record = await scrape_single_company(
            page,
            item["linkedin_url"],
            company_name=item["company_name"],
        )
        fields = _record_fields(record)
        outcome["parsed_fields"] = fields
        outcome["enrichment_field_count"] = _enrichment_field_count(fields)
        outcome["scrape_status"] = record.scrape_status
        outcome["scrape_error"] = record.scrape_error
        outcome["success"] = _is_success(fields, scrape_status=record.scrape_status)

        print(f"PARSE RESULT: status={record.scrape_status}", flush=True)
        for key in ENRICHMENT_FIELDS:
            val = fields.get(key)
            display = (str(val)[:120] + "…") if val and len(str(val)) > 120 else val
            print(f"  {key}: {display}", flush=True)
        if outcome["success"]:
            print(f"RESULT: SUCCESS ({outcome['enrichment_field_count']} fields)", flush=True)
        else:
            print(f"RESULT: FAIL (insufficient fields or scrape error)", flush=True)

    except ProfileExpiredError as exc:
        outcome["scrape_status"] = "error"
        outcome["scrape_error"] = str(exc)
        outcome["exception_type"] = type(exc).__name__
        outcome["exception_traceback"] = traceback.format_exc()
        print(f"EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        raise
    except Exception as exc:
        outcome["scrape_status"] = "error"
        outcome["scrape_error"] = str(exc)
        outcome["exception_type"] = type(exc).__name__
        outcome["exception_traceback"] = traceback.format_exc()
        print(f"EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()

    return outcome


async def run_smoke_test(*, headless: bool = True, delay_seconds: float = 3.0) -> dict[str, Any]:
    ensure_scraper_dependencies()

    if not profile_is_initialized():
        print_profile_refresh_message()
        raise ProfileExpiredError("Browser profile not initialized.")

    results: list[dict[str, Any]] = []

    async with persistent_browser_context(headless=headless) as page:
        for index, item in enumerate(CONFIRMED_COMPANIES):
            try:
                outcome = await _scrape_one(page, item)
            except ProfileExpiredError:
                report = _build_report(results, pipeline_ready=False, aborted=True)
                _write_reports(report)
                raise
            results.append(outcome)
            if index < len(CONFIRMED_COMPANIES) - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    success_count = sum(1 for r in results if r.get("success"))
    pipeline_ready = success_count >= 3
    report = _build_report(results, pipeline_ready=pipeline_ready, aborted=False)
    _write_reports(report)
    return report


def _build_report(
    results: list[dict[str, Any]],
    *,
    pipeline_ready: bool,
    aborted: bool,
) -> dict[str, Any]:
    success_count = sum(1 for r in results if r.get("success"))
    return {
        "schema_version": "1.0.0",
        "report_type": "linkedin_authenticated_smoke_test",
        "generated_at": _utc_now(),
        "read_only": True,
        "db_writes": False,
        "auth_mode": "playwright_persistent_profile",
        "companies_tested": len(CONFIRMED_COMPANIES),
        "companies_completed": len(results),
        "success_count": success_count,
        "success_threshold": 3,
        "pipeline_ready_for_validation_500": pipeline_ready,
        "pipeline_status": "READY" if pipeline_ready else "NOT_READY",
        "aborted": aborted,
        "results": results,
    }


def _write_reports(report: dict[str, Any]) -> None:
    SMOKE_TEST_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        "# LinkedIn Authenticated Smoke Test",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "> Research only. No Registry or production DB writes.",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Companies tested | {report['companies_tested']} |",
        f"| Companies completed | {report['companies_completed']} |",
        f"| Successful enrichments | {report['success_count']} |",
        f"| Success threshold | {report['success_threshold']} |",
        f"| **Pipeline status** | **{report['pipeline_status']}** |",
        "",
    ]

    if report["pipeline_ready_for_validation_500"]:
        lines.append(
            "The authenticated pipeline is **READY** for the 500-company validation batch."
        )
    else:
        lines.append(
            "The authenticated pipeline is **NOT READY**. Do not run the 500-company "
            "validation until root causes below are resolved."
        )

    lines.extend(["", "## Per-company results", ""])

    for row in report["results"]:
        fields = row.get("parsed_fields") or {}
        ver = row.get("verification") or {}
        status = "SUCCESS" if row.get("success") else "FAIL"
        lines.append(f"### {row['company_name']} — {status}")
        lines.append("")
        lines.append(f"- **LinkedIn URL:** {row.get('linkedin_url')}")
        lines.append(f"- **URL source:** {row.get('url_source')}")
        lines.append(f"- **Navigation success:** {ver.get('navigation_success')}")
        lines.append(f"- **Verified official page:** {ver.get('verified_official_page')}")
        lines.append(f"- **Final URL:** {ver.get('final_url')}")
        lines.append(f"- **HTTP status:** {ver.get('http_status')}")
        lines.append(f"- **Scrape status:** {row.get('scrape_status')}")
        if row.get("scrape_error"):
            lines.append(f"- **Error:** {row.get('scrape_error')}")
        if row.get("exception_traceback"):
            lines.append("")
            lines.append("```")
            lines.append(row["exception_traceback"].strip())
            lines.append("```")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        for key in ENRICHMENT_FIELDS:
            val = fields.get(key) or "—"
            val = str(val).replace("|", "\\|")
            if len(val) > 100:
                val = val[:100] + "…"
            lines.append(f"| {key} | {val} |")
        lines.append("")

    SMOKE_TEST_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    try:
        report = asyncio.run(run_smoke_test(headless=args.headless, delay_seconds=args.delay))
    except ProfileExpiredError:
        return 1

    print(f"\n{'=' * 72}", flush=True)
    print(
        f"SMOKE TEST COMPLETE: {report['success_count']}/{report['companies_tested']} succeeded",
        flush=True,
    )
    print(f"Pipeline status: {report['pipeline_status']}", flush=True)
    print(f"Wrote {SMOKE_TEST_JSON}", flush=True)
    print(f"Wrote {SMOKE_TEST_MD}", flush=True)
    return 0 if report["pipeline_ready_for_validation_500"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
