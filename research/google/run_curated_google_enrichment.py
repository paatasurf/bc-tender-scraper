#!/usr/bin/env python3
"""Curated Google Business enrichment for Class A/B companies — research only."""

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

from config.env import load_app_env  # noqa: E402
from pipeline.google_enrichment.provider import NullProvider, get_provider  # noqa: E402
from research.google.curated_google_enrichment import (  # noqa: E402
    GOOGLE_FAILED_STATUS,
    GOOGLE_NOT_FOUND_STATUS,
    GOOGLE_VERIFIED_STATUS,
    GoogleEnrichmentRecord,
    compute_class_statistics,
    compute_run_statistics,
    enrich_company_async,
    load_curated_companies,
)
from research.google.paths import (  # noqa: E402
    CURATED_GOOGLE_REPORT_JSON,
    CURATED_GOOGLE_REPORT_MD,
    CURATED_GOOGLE_STATS_JSON,
)


def _write_reports(
    results: list[GoogleEnrichmentRecord],
    *,
    started_at: str,
    args: argparse.Namespace,
    provider_name: str,
) -> dict[str, Any]:
    stats = compute_class_statistics(results)
    run_stats = compute_run_statistics(results)
    generated_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "schema_version": "1.0.0",
        "report_type": "curated_google_enrichment",
        "generated_at": generated_at,
        "started_at": started_at,
        "read_only": True,
        "db_writes": False,
        "creates_new_companies": False,
        "data_source": "research/enrichment/company_profiles.json",
        "provider": provider_name,
        "status_values": {
            "google_verified": GOOGLE_VERIFIED_STATUS,
            "not_found": GOOGLE_NOT_FOUND_STATUS,
            "failed": GOOGLE_FAILED_STATUS,
            "not_run": None,
        },
        "options": {
            "limit": args.limit,
            "with_website_only": args.with_website_only,
            "dry_run": args.dry_run,
            "classes": list(args.classes),
        },
        "statistics": run_stats,
        "statistics_by_class": stats,
        "results": [row.to_dict() for row in results],
    }

    CURATED_GOOGLE_REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    CURATED_GOOGLE_STATS_JSON.write_text(
        json.dumps({"generated_at": generated_at, "statistics": run_stats, "statistics_by_class": stats}, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Curated Google Business Enrichment Report",
        "",
        f"Generated: {generated_at}",
        "",
        "> Research only. Enriches existing Class A/B companies — no new records, no DB writes.",
        "",
        f"Provider: **{provider_name}**",
        "",
        "## Run totals",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Total processed | {run_stats['total_companies_processed']} |",
        f"| Google Verified | {run_stats['google_verified']} |",
        f"| Not Found | {run_stats['not_found']} |",
        f"| Failed | {run_stats['failed']} |",
        f"| Skipped | {run_stats['skipped']} |",
        "",
    ]
    CURATED_GOOGLE_REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    return payload


async def _run_batch(
    companies,
    *,
    dry_run: bool,
) -> tuple[list[GoogleEnrichmentRecord], str]:
    provider = NullProvider() if dry_run else get_provider()
    reserved: set[str] = set()
    results: list[GoogleEnrichmentRecord] = []

    for index, company in enumerate(companies, start=1):
        result = await enrich_company_async(
            company,
            provider=provider,
            run_lookup=not dry_run,
            reserved_place_ids=frozenset(reserved),
        )
        if result.google_verified and result.google_place_id:
            reserved.add(result.google_place_id)
        results.append(result)
        if index % 5 == 0 or index == len(companies):
            print(f"Processed {index}/{len(companies)}", file=sys.stderr)

    return results, provider.provider_name


def main() -> int:
    load_app_env()
    parser = argparse.ArgumentParser(description="Curated Google Business enrichment (Class A/B).")
    parser.add_argument("--classes", nargs="+", choices=["A", "B"], default=["A", "B"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--with-website-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Skip provider lookups (status stays null)")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    companies = load_curated_companies(
        source_classes=tuple(args.classes),
        with_website_only=args.with_website_only,
    )
    if args.limit > 0:
        companies = companies[: args.limit]

    print(
        f"Curated Google enrichment: {len(companies)} companies "
        f"(classes={','.join(args.classes)}, dry_run={args.dry_run})",
        file=sys.stderr,
    )

    results, provider_name = asyncio.run(_run_batch(companies, dry_run=args.dry_run))
    report = _write_reports(results, started_at=started_at, args=args, provider_name=provider_name)

    totals = report["statistics"]
    print(
        f"Totals: processed={totals['total_companies_processed']} "
        f"verified={totals['google_verified']} "
        f"not_found={totals['not_found']} "
        f"failed={totals['failed']} "
        f"skipped={totals['skipped']}",
        file=sys.stderr,
    )
    print(f"Wrote {CURATED_GOOGLE_REPORT_JSON}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
