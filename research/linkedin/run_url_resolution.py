#!/usr/bin/env python3
"""Resolve LinkedIn company URLs — production-grade multi-stage pipeline.

DEPRECATED for discovery: prefer `run_curated_verification.py`, which uses the
curated company_profiles database (Class A/B) and does not force generic LinkedIn matches.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.paths import (  # noqa: E402
    URL_CACHE_JSON,
    URL_RESOLUTION_REPORT_JSON,
    URL_RESOLUTION_REPORT_MD,
    URL_RESOLUTION_STATISTICS_JSON,
)
from research.linkedin.url_resolver import (  # noqa: E402
    ENRICHMENT_CONFIDENCE_THRESHOLD,
    build_association_targets,
    build_resolution_targets,
    compute_benchmark_statistics,
    resolve_batch_async,
)


def _write_reports(
    results: list,
    *,
    started_at: str,
    pool_size: int,
    benchmark_label: str,
) -> dict:
    enrichable = [r for r in results if r.enrichable]
    rejected = [r for r in results if r.verification_status == "rejected"]
    unresolved = [r for r in results if r.verification_status == "unresolved"]
    cache_hits = [r for r in results if r.search_skipped]
    method_counts = Counter(r.match_method for r in enrichable)
    stage_counts = Counter(r.resolution_stage for r in enrichable)

    stats = compute_benchmark_statistics(
        results,
        pool_size=pool_size,
        cache_hits=len(cache_hits),
    )
    stats["benchmark_label"] = benchmark_label
    stats["generated_at"] = datetime.now(timezone.utc).isoformat()
    stats["started_at"] = started_at

    URL_RESOLUTION_STATISTICS_JSON.write_text(
        json.dumps(stats, indent=2, default=str),
        encoding="utf-8",
    )

    report = {
        "schema_version": "2.0.0",
        "report_type": "linkedin_url_resolution",
        "generated_at": stats["generated_at"],
        "started_at": started_at,
        "benchmark_label": benchmark_label,
        "read_only": True,
        "db_writes": False,
        "confidence_threshold": ENRICHMENT_CONFIDENCE_THRESHOLD,
        "summary": {
            "total_processed": len(results),
            "urls_resolved": stats["urls_resolved"],
            "verified_enrichable": stats["verified_enrichable"],
            "verified_rate_pct": stats["verified_rate_pct"],
            "false_matches": stats["false_matches"],
            "unresolved_count": stats["unresolved"],
            "rejected_count": stats["rejected"],
            "average_confidence": stats["average_confidence"],
            "cache_hit_rate_pct": stats["cache_hit_rate_pct"],
            "estimated_full_pool_coverage_pct": stats["estimated_full_pool_coverage_pct"],
            "meets_95pct_target": stats["meets_target"],
            "match_methods": dict(method_counts),
            "resolution_stages": dict(stage_counts),
        },
        "statistics": stats,
        "results": [r.to_dict() for r in results],
    }

    URL_RESOLUTION_REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        "# LinkedIn URL Resolution Report",
        "",
        f"Generated: {report['generated_at']}",
        f"Benchmark: **{benchmark_label}**",
        "",
        "> Research only. No Registry or production DB writes.",
        "",
        f"Enrichment queue requires **URL confidence >= {ENRICHMENT_CONFIDENCE_THRESHOLD}**.",
        "",
        "## Benchmark summary",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Companies processed | {stats['benchmark_size']} |",
        f"| Association pool size | {stats['association_pool_size']} |",
        f"| URLs resolved | {stats['urls_resolved']} |",
        f"| Verified enrichable (>= {ENRICHMENT_CONFIDENCE_THRESHOLD}) | {stats['verified_enrichable']} |",
        f"| Verified rate | {stats['verified_rate_pct']}% |",
        f"| False matches | {stats['false_matches']} |",
        f"| Unresolved | {stats['unresolved']} |",
        f"| Rejected | {stats['rejected']} |",
        f"| Average confidence | {stats['average_confidence']} |",
        f"| Cache hit rate | {stats['cache_hit_rate_pct']}% |",
        f"| Est. full-pool coverage | {stats['estimated_full_pool_coverage_pct']}% |",
        f"| Meets 95% target | {'YES' if stats['meets_target'] else 'NO'} |",
        "",
        "### Resolution stages (verified)",
        "",
    ]
    for stage, count in sorted(stage_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- **{stage or 'unknown'}**: {count}")

    lines.extend(["", "### Match methods (verified)", ""])
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- **{method}**: {count}")

    lines.extend(["", "## Verified companies (sample)", ""])
    for row in enrichable[:50]:
        lines.append(
            f"- **{row.company_name}** — {row.url_confidence}% "
            f"({row.resolution_stage}) — {row.canonical_linkedin_url}"
        )
    if len(enrichable) > 50:
        lines.append(f"- … and {len(enrichable) - 50} more")

    lines.extend(["", "## Unresolved / rejected (sample)", ""])
    for row in (unresolved + rejected)[:30]:
        lines.append(
            f"- **{row.company_name}** — {row.verification_status} — "
            f"{row.rejection_reason or row.match_reason}"
        )

    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- URL cache: `{URL_CACHE_JSON}`",
            f"- Statistics: `{URL_RESOLUTION_STATISTICS_JSON}`",
            f"- JSON report: `{URL_RESOLUTION_REPORT_JSON}`",
        ]
    )

    URL_RESOLUTION_REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--refresh", action="store_true", help="Re-resolve even if cached.")
    parser.add_argument(
        "--association-only",
        action="store_true",
        help="Benchmark on BC association members only.",
    )
    args = parser.parse_args()

    if args.association_only:
        pool = build_association_targets()
        label = "bc_association_members"
    else:
        pool = build_resolution_targets(bc_construction_only=True)
        label = "full_bc_construction_pool"

    batch = pool[args.offset : args.offset + args.limit]
    if not batch:
        print("[url-resolution] Nothing to process.", flush=True)
        return 0

    started_at = datetime.now(timezone.utc).isoformat()
    print(
        f"[url-resolution] {label} processing {len(batch)} companies "
        f"(offset={args.offset}, pool={len(pool)})",
        flush=True,
    )

    results = asyncio.run(
        resolve_batch_async(
            batch,
            refresh=args.refresh,
            verify_live=True,
        )
    )

    report = _write_reports(results, started_at=started_at, pool_size=len(pool), benchmark_label=label)
    s = report["summary"]
    print(
        f"[url-resolution] verified={s['verified_enrichable']}/{s['total_processed']} "
        f"({s['verified_rate_pct']}%) avg_conf={s['average_confidence']} "
        f"cache_hits={s['cache_hit_rate_pct']}%",
        flush=True,
    )
    print(f"[url-resolution] wrote {URL_CACHE_JSON}", flush=True)
    print(f"[url-resolution] wrote {URL_RESOLUTION_STATISTICS_JSON}", flush=True)
    print(f"[url-resolution] wrote {URL_RESOLUTION_REPORT_JSON}", flush=True)
    print(f"[url-resolution] wrote {URL_RESOLUTION_REPORT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
