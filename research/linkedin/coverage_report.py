"""Generate coverage_report.md from enriched company dataset."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import COMPANIES_ENRICHED_JSON, COVERAGE_REPORT_MD


TRACKED_FIELDS = (
    "linkedin_url",
    "website",
    "industry",
    "company_size",
    "headquarters",
    "founded_year",
    "description",
    "specialties",
    "locations",
    "followers",
)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100.0 * n / total:.1f}%"


def _field_populated(rec: dict[str, Any], field: str) -> bool:
    value = rec.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _top_counter(records: list[dict], field: str, *, limit: int = 15) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for rec in records:
        value = rec.get(field)
        if not value or not str(value).strip():
            continue
        counter[str(value).strip()] += 1
    return counter.most_common(limit)


def build_coverage_report(records: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    total = len(records)
    verified = [r for r in records if r.get("linkedin_enrichment_status") == "verified"]
    verified_count = len(verified)
    with_website = sum(1 for r in records if _field_populated(r, "website"))
    with_followers = sum(1 for r in records if _field_populated(r, "followers"))
    with_linkedin_industry = sum(1 for r in verified if _field_populated(r, "industry"))
    with_linkedin_size = sum(1 for r in verified if _field_populated(r, "company_size"))
    with_founded = sum(1 for r in verified if _field_populated(r, "founded_year"))

    lines = [
        "# LinkedIn Company Enrichment — Coverage Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Dataset version: {metadata.get('version', 'unknown')}",
        "",
        "## Summary",
        "",
        f"| Metric | Count | Share |",
        f"|--------|------:|------:|",
        f"| Total companies in pool | {total:,} | 100% |",
        f"| LinkedIn pages verified (public fetch OK) | {verified_count:,} | {_pct(verified_count, total)} |",
        f"| Companies with website | {with_website:,} | {_pct(with_website, total)} |",
        f"| Companies with follower count (LinkedIn) | {with_followers:,} | {_pct(with_followers, total)} |",
        "",
        "### LinkedIn enrichment status",
        "",
    ]
    status = metadata.get("linkedin_enrichment_status") or {}
    for label in ("verified", "attempted_failed", "not_attempted"):
        count = status.get(label, 0)
        lines.append(f"- **{label}**: {count:,} ({_pct(count, total)})")

    lines.extend(
        [
            "",
            "## Field completeness",
            "",
            "| Field | Populated | Share |",
            "|-------|----------:|------:|",
        ]
    )
    for field in TRACKED_FIELDS:
        count = sum(1 for r in records if _field_populated(r, field))
        lines.append(f"| {field} | {count:,} | {_pct(count, total)} |")

    lines.extend(["", "## Company size distribution (LinkedIn verified only)", ""])
    size_dist = _top_counter(verified, "company_size", limit=20)
    if size_dist:
        lines.append("| Company size | Count |")
        lines.append("|--------------|------:|")
        for label, count in size_dist:
            lines.append(f"| {label} | {count:,} |")
    else:
        lines.append("_No company size data populated in this run._")

    lines.extend(["", "## Industry distribution (LinkedIn verified only)", ""])
    industry_dist = _top_counter(verified, "industry", limit=20)
    if industry_dist:
        lines.append("| Industry | Count |")
        lines.append("|----------|------:|")
        for label, count in industry_dist:
            lines.append(f"| {label} | {count:,} |")
    else:
        lines.append("_No LinkedIn industry data in verified pages._")

    lines.extend(["", "## Source industry codes (ODB NAICS, all pool)", ""])
    source_industry_dist = _top_counter(records, "source_industry_code", limit=15)
    if source_industry_dist:
        lines.append("| NAICS / source code | Count |")
        lines.append("|---------------------|------:|")
        for label, count in source_industry_dist:
            lines.append(f"| {label} | {count:,} |")
    else:
        lines.append("_No source industry codes._")

    lines.extend(["", "## Founded year coverage (LinkedIn verified only)", ""])
    founded_values = [r.get("founded_year") for r in verified if _field_populated(r, "founded_year")]
    founded_count = len(founded_values)
    lines.append(
        f"- Verified pages with founded year: **{founded_count:,}** "
        f"({_pct(founded_count, verified_count) if verified_count else '0.0%'} of verified, "
        f"{_pct(founded_count, total)} of pool)"
    )
    if founded_values:
        decade_counter: Counter[str] = Counter()
        for year in founded_values:
            try:
                decade = f"{int(str(year)[:4]) // 10 * 10}s"
            except ValueError:
                continue
            decade_counter[decade] += 1
        lines.append("")
        lines.append("| Decade | Count |")
        lines.append("|--------|------:|")
        for decade, count in sorted(decade_counter.items()):
            lines.append(f"| {decade} | {count:,} |")

    lines.extend(["", "## Provenance source mix", ""])
    source_counter: Counter[str] = Counter()
    for rec in records:
        for src in rec.get("provenance_sources") or []:
            source_counter[src] += 1
    lines.append("| Source | Companies |")
    lines.append("|--------|----------:|")
    for src, count in source_counter.most_common():
        lines.append(f"| {src} | {count:,} |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Read-only research artifact; no database writes or Market Registry integration.",
            "- LinkedIn public fetch without session yields limited fields; most enrichment comes from local source snapshots.",
            f"- Raw LinkedIn cache: `{metadata.get('raw_artifact', 'linkedin_companies_raw.json')}`",
            "",
        ]
    )
    return "\n".join(lines)


def write_coverage_report(
    *,
    enriched_path: Path | None = None,
    out_path: Path | None = None,
) -> str:
    enriched_path = enriched_path or COMPANIES_ENRICHED_JSON
    out_path = out_path or COVERAGE_REPORT_MD
    payload = json.loads(enriched_path.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    metadata = payload.get("metadata") or {}
    report = build_coverage_report(records, metadata)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return report
