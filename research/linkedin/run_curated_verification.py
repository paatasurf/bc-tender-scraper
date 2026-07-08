#!/usr/bin/env python3
"""Verify LinkedIn pages for curated Class A/B companies — no generic search."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.curated_verifier import (  # noqa: E402
    LINKEDIN_FAILED_STATUS,
    LINKEDIN_NOT_FOUND_STATUS,
    LINKEDIN_VERIFIED_STATUS,
    CompanyVerificationResult,
    compute_class_statistics,
    compute_run_statistics,
    load_curated_companies,
    verify_company_async,
)
from research.linkedin.paths import (  # noqa: E402
    CURATED_VERIFICATION_JSON,
    CURATED_VERIFICATION_MD,
    CURATED_VERIFICATION_STATS_JSON,
)
from research.linkedin.session import ProfileExpiredError  # noqa: E402


def _write_reports(
    results: list[CompanyVerificationResult],
    *,
    started_at: str,
    args: argparse.Namespace,
    batch_error: str | None = None,
) -> dict[str, Any]:
    stats = compute_class_statistics(results)
    run_stats = compute_run_statistics(results)
    generated_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "schema_version": "1.2.0",
        "report_type": "curated_linkedin_verification",
        "generated_at": generated_at,
        "started_at": started_at,
        "read_only": True,
        "db_writes": False,
        "batch_error": batch_error,
        "objective": "confirm_official_linkedin_pages_from_curated_sources_only",
        "scope": {
            "source_classes": list(args.classes),
            "ignored_source_classes": ["C", "D", "E"],
            "data_source": "research/enrichment/company_profiles.json",
            "generic_linkedin_resolver": False,
            "creates_new_companies": False,
        },
        "status_values": {
            "linkedin_verified": LINKEDIN_VERIFIED_STATUS,
            "not_found": LINKEDIN_NOT_FOUND_STATUS,
            "failed": LINKEDIN_FAILED_STATUS,
            "not_run": None,
        },
        "options": {
            "limit": args.limit,
            "with_website_only": args.with_website_only,
            "deep_website_scan": args.deep_website_scan,
            "confirm_linkedin": args.confirm_linkedin,
        },
        "statistics": run_stats,
        "statistics_by_class": stats,
        "results": [row.to_dict() for row in results],
    }

    CURATED_VERIFICATION_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    CURATED_VERIFICATION_STATS_JSON.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "batch_error": batch_error,
                "statistics": run_stats,
                "statistics_by_class": stats,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# Curated LinkedIn Verification Report",
        "",
        f"Generated: {generated_at}",
        "",
        "> Research only. Existing curated companies only — no new records, no generic search.",
        "",
    ]
    if batch_error:
        lines.extend([f"> **Batch error:** {batch_error}", ""])

    lines.extend(
        [
            "## Status rules",
            "",
            f"- **`{LINKEDIN_VERIFIED_STATUS}`** + `linkedin_verified: true` — confirmed official page; URL stored",
            f"- **`{LINKEDIN_NOT_FOUND_STATUS}`** + `linkedin_verified: false` — verification completed, no valid page",
            f"- **`{LINKEDIN_FAILED_STATUS}`** + `linkedin_verified: false` — verification error on candidate",
            "- **`null`** + `linkedin_verified: false` — verification not run (skipped)",
            "",
            "## Run totals",
            "",
            "| Metric | Count |",
            "|--------|------:|",
            f"| Total companies processed | {run_stats['total_companies_processed']} |",
            f"| LinkedIn Verified | {run_stats['linkedin_verified']} |",
            f"| Not Found | {run_stats['not_found']} |",
            f"| Failed | {run_stats['failed']} |",
            f"| Skipped | {run_stats['skipped']} |",
            "",
            "## Scope",
            "",
            f"- Classes processed: **{', '.join(args.classes)}**",
            "- LinkedIn sources: curated profile URL + links on curated official website only",
            "",
            "## Statistics by class",
            "",
        ]
    )
    for source_class in sorted(stats):
        row = stats[source_class]
        lines.extend(
            [
                f"### Class {source_class}",
                "",
                "| Metric | Count |",
                "|--------|------:|",
                f"| Total companies | {row['total_companies']} |",
                f"| LinkedIn Verified | {row['linkedin_verified']} |",
                f"| Not Found | {row['not_found']} |",
                f"| Failed | {row['failed']} |",
                f"| Skipped | {row['skipped']} |",
                f"| LinkedIn Verified rate | {row['linkedin_verified_pct']}% |",
                "",
            ]
        )

    CURATED_VERIFICATION_MD.write_text("\n".join(lines), encoding="utf-8")
    return payload


async def _run_batch(
    companies,
    *,
    confirm_linkedin: bool,
    deep_website_scan: bool,
) -> list[CompanyVerificationResult]:
    if not companies:
        return []

    if confirm_linkedin:
        from research.linkedin.scraper.persistent_browser import persistent_browser_context

        async with persistent_browser_context(headless=True) as page:
            results: list[CompanyVerificationResult] = []
            for index, company in enumerate(companies, start=1):
                result = await verify_company_async(
                    company,
                    page=page,
                    deep_website_scan=deep_website_scan,
                    confirm_linkedin=True,
                )
                results.append(result)
                if index % 10 == 0 or index == len(companies):
                    print(f"Processed {index}/{len(companies)}", file=sys.stderr)
            return results

    results = []
    for index, company in enumerate(companies, start=1):
        result = await verify_company_async(
            company,
            page=None,
            deep_website_scan=deep_website_scan,
            confirm_linkedin=False,
        )
        results.append(result)
        if index % 50 == 0 or index == len(companies):
            print(f"Processed {index}/{len(companies)}", file=sys.stderr)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Confirm LinkedIn pages for curated Class A/B companies."
    )
    parser.add_argument(
        "--classes",
        nargs="+",
        choices=["A", "B"],
        default=["A", "B"],
        help="Source confidence classes to process (default: A B)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit companies processed (0 = all)")
    parser.add_argument(
        "--with-website-only",
        action="store_true",
        help="Process only curated companies that have an official website on record",
    )
    parser.add_argument(
        "--deep-website-scan",
        action="store_true",
        help="Scan contact/about pages on curated official website (slower)",
    )
    parser.add_argument(
        "--no-confirm-linkedin",
        action="store_true",
        help="Skip authenticated LinkedIn confirmation (status remains null / skipped)",
    )
    args = parser.parse_args()
    args.confirm_linkedin = not args.no_confirm_linkedin

    started_at = datetime.now(timezone.utc).isoformat()
    companies = load_curated_companies(
        source_classes=tuple(args.classes),
        with_website_only=args.with_website_only,
    )
    if args.limit > 0:
        companies = companies[: args.limit]

    print(
        f"Curated LinkedIn verification: {len(companies)} companies "
        f"(classes={','.join(args.classes)}, confirm={args.confirm_linkedin})",
        file=sys.stderr,
    )

    batch_error: str | None = None
    results: list[CompanyVerificationResult] = []
    try:
        results = asyncio.run(
            _run_batch(
                companies,
                confirm_linkedin=args.confirm_linkedin,
                deep_website_scan=args.deep_website_scan,
            )
        )
    except ProfileExpiredError as exc:
        batch_error = str(exc)
        print(f"BATCH FAILED: {batch_error}", file=sys.stderr)
        return 2

    report = _write_reports(results, started_at=started_at, args=args, batch_error=batch_error)

    totals = report["statistics"]
    print(
        f"Totals: processed={totals['total_companies_processed']} "
        f"verified={totals['linkedin_verified']} "
        f"not_found={totals['not_found']} "
        f"failed={totals['failed']} "
        f"skipped={totals['skipped']}",
        file=sys.stderr,
    )

    for source_class in sorted(report["statistics_by_class"]):
        row = report["statistics_by_class"][source_class]
        print(
            f"Class {source_class}: total={row['total_companies']} "
            f"verified={row['linkedin_verified']} "
            f"not_found={row['not_found']} "
            f"failed={row['failed']} "
            f"skipped={row['skipped']}",
            file=sys.stderr,
        )

    print(f"Wrote {CURATED_VERIFICATION_JSON}", file=sys.stderr)
    print(f"Wrote {CURATED_VERIFICATION_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
