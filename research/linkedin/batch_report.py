"""Generate batch_report.json and batch_report.md after each authenticated run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from research.linkedin.paths import BATCH_REPORT_JSON, BATCH_REPORT_MD

ENRICHMENT_FIELDS = (
    "website",
    "industry",
    "company_size",
    "headquarters",
    "founded",
    "description",
    "specialties",
    "location",
)


def _field_populated(record: dict[str, Any], field: str) -> bool:
    value = record.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def build_batch_report(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_batch_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "db_writes": False,
        **stats,
    }


def render_batch_report_md(report: dict[str, Any]) -> str:
    lines = [
        "# LinkedIn Batch Report",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Auth mode: {report.get('auth_mode')}",
        f"Batch offset: {report.get('offset')} | limit: {report.get('limit')}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Processed this batch | {report.get('processed', 0)} |",
        f"| Cached (skipped) | {report.get('cached_skipped', 0)} |",
        f"| Scraped OK | {report.get('scraped_ok', 0)} |",
        f"| Failed (permanent) | {report.get('failed_permanent', 0)} |",
        f"| Failed (after retries) | {report.get('failed_transient', 0)} |",
        f"| New LinkedIn pages verified | {report.get('new_linkedin_pages', 0)} |",
        "",
        "## Enrichment fields added (this batch)",
        "",
    ]
    fields_added = report.get("enrichment_fields_added") or {}
    if fields_added:
        lines.extend(["| Field | Records populated |", "|-------|------------------:|"])
        for field, count in fields_added.items():
            lines.append(f"| {field} | {count} |")
    else:
        lines.append("_No new enrichment fields in this batch._")

    errors = report.get("errors") or []
    lines.extend(["", "## Errors (this batch)", ""])
    if errors:
        for item in errors[:25]:
            lines.append(f"- **{item.get('company_name')}**: {item.get('error')}")
        if len(errors) > 25:
            lines.append(f"- … and {len(errors) - 25} more")
    else:
        lines.append("_No errors in this batch._")

    lines.extend(
        [
            "",
            "## Resume",
            "",
            f"Next offset: **{report.get('next_offset', 0)}**",
            f"Remaining in queue: **{report.get('remaining', 0)}**",
            "",
            "Re-run the same command to continue, or pass `--offset` explicitly.",
            "",
        ]
    )
    return "\n".join(lines)


def write_batch_reports(stats: dict[str, Any]) -> tuple[dict[str, Any], str]:
    report = build_batch_report(stats)
    BATCH_REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = render_batch_report_md(report)
    BATCH_REPORT_MD.write_text(md, encoding="utf-8")
    return report, md


def count_enrichment_fields(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rec in records:
        for field in ENRICHMENT_FIELDS:
            if _field_populated(rec, field):
                counts[field] = counts.get(field, 0) + 1
    return counts
