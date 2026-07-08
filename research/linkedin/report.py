"""Step 6 — Generate report.md and report.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import REPORT_JSON, REPORT_MD


def build_report(
    *,
    raw: dict[str, Any],
    normalized: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    top_new = (comparison.get("potentially_new") or [])[:100]
    return {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_discovery_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "db_writes": False,
        "disclaimer": "Experimental research only — not Registry Engine, not production.",
        "discovery": {
            "mode": raw.get("mode"),
            "library": raw.get("library"),
            "total_companies_collected": raw.get("record_count", 0),
            "scrape_ok": sum(1 for r in raw.get("records") or [] if r.get("scrape_status") == "ok"),
            "scrape_errors": sum(1 for r in raw.get("records") or [] if r.get("scrape_status") == "error"),
        },
        "market_registry_snapshot_pool_size": comparison.get("market_registry_pool_size"),
        "matched_companies": comparison.get("already_known_count"),
        "potentially_new_companies": comparison.get("potentially_new_count"),
        "possible_duplicates": comparison.get("possible_duplicates_count"),
        "top_100_highest_confidence_new_discoveries": [
            {
                "company_name": r.get("company_name"),
                "normalized_name_key": r.get("normalized_name_key"),
                "linkedin_company_url": r.get("linkedin_company_url"),
                "website": r.get("website"),
                "industry": r.get("industry"),
                "headquarters": r.get("headquarters"),
                "discovery_confidence": r.get("discovery_confidence"),
            }
            for r in top_new
        ],
        "comparison": comparison,
    }


def render_markdown(report: dict[str, Any]) -> str:
    disc = report.get("discovery") or {}
    lines = [
        "# LinkedIn Company Discovery — Research Report",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "> **Experimental research only.** No database writes. No Registry Engine integration.",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|------:|",
        f"| Total companies collected | {disc.get('total_companies_collected', 0)} |",
        f"| Scrape OK | {disc.get('scrape_ok', 0)} |",
        f"| Scrape errors | {disc.get('scrape_errors', 0)} |",
        f"| Market Registry snapshot pool | {report.get('market_registry_snapshot_pool_size', 0)} |",
        f"| Matched (already known) | {report.get('matched_companies', 0)} |",
        f"| Potentially new | {report.get('potentially_new_companies', 0)} |",
        f"| Possible duplicates (within LinkedIn set) | {report.get('possible_duplicates', 0)} |",
        "",
        f"Discovery mode: `{disc.get('mode')}` · Library: `{disc.get('library')}`",
        "",
        "## Top 100 highest-confidence new discoveries",
        "",
    ]
    top = report.get("top_100_highest_confidence_new_discoveries") or []
    if not top:
        lines.append("_None — all records matched the Market Registry snapshot or lacked name keys._")
    else:
        lines.append("| Confidence | Company | Website | Industry | LinkedIn |")
        lines.append("|-----------:|---------|---------|----------|----------|")
        for row in top:
            lines.append(
                f"| {row.get('discovery_confidence', '')} "
                f"| {row.get('company_name') or '—'} "
                f"| {row.get('website') or '—'} "
                f"| {row.get('industry') or '—'} "
                f"| {row.get('linkedin_company_url') or '—'} |"
            )
    lines.extend(["", "## Library selection", "", "See [LIBRARY_EVALUATION.md](./LIBRARY_EVALUATION.md).", ""])
    return "\n".join(lines)


def write_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    return REPORT_JSON, REPORT_MD
