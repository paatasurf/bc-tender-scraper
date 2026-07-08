"""Generate validation report for the first-N authenticated LinkedIn batch."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from research.linkedin.batch_runner import QueueItem, build_queue
from research.linkedin.company_cache import load_cached_company
from research.linkedin.paths import VALIDATION_500_JSON, VALIDATION_500_MD
from research.linkedin.session import profile_is_initialized, resolve_auth_mode

LOGIN_WALL_MARKERS = (
    "login",
    "authwall",
    "checkpoint",
    "http 999",
    "bot detection",
    "sign in",
)
NOT_FOUND_MARKERS = (
    "404",
    "not found",
    "page not found",
    "invalid url",
    "no company",
    "could not find",
)


def _populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _classify_error(error: str | None) -> str:
    if not error:
        return "unknown"
    lower = error.lower()
    if any(m in lower for m in NOT_FOUND_MARKERS):
        return "not_found"
    if any(m in lower for m in LOGIN_WALL_MARKERS):
        return "login_wall"
    return "other"


def _baseline_completeness(item: QueueItem) -> int:
    score = 0
    if item.source_website:
        score += 15
    if item.linkedin_url:
        score += 10
    if item.source_city:
        score += 10
    if any(s.startswith("association_") for s in item.provenance_sources):
        score += 15
    if "enterprise_seed" in item.provenance_sources:
        score += 15
    if "odbus_bc_naics23" in item.provenance_sources:
        score += 10
    return min(100, score)


def _linkedin_completeness(item: QueueItem, record: dict[str, Any] | None) -> int:
    score = _baseline_completeness(item)
    if not record:
        return score
    if record.get("scrape_status") != "ok":
        return score

    score = min(100, score + 10)
    for field, points in (
        ("website", 5),
        ("industry", 10),
        ("company_size", 5),
        ("founded", 5),
        ("founded_year", 5),
        ("description", 5),
        ("specialties", 5),
    ):
        if _populated(record.get(field)):
            score = min(100, score + points)

    followers = (record.get("source_fields") or {}).get("followers") or record.get("followers")
    if _populated(followers):
        score = min(100, score + 5)
    if record.get("linkedin_page_verified"):
        score = min(100, score + 5)
    return score


def _parse_followers(record: dict[str, Any]) -> int | None:
    followers = (record.get("source_fields") or {}).get("followers") or record.get("followers")
    if followers is not None:
        try:
            return int(followers)
        except (TypeError, ValueError):
            pass
    desc = record.get("description") or ""
    match = re.search(r"([\d,]+)\s+followers?", desc, re.I)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def generate_validation_500_report(
    *,
    sample_size: int = 500,
    bc_construction_only: bool = True,
) -> dict[str, Any]:
    queue = build_queue(bc_construction_only=bc_construction_only)
    sample = queue[:sample_size]
    total_pool = len(queue)

    results: list[dict[str, Any]] = []
    ok_count = 0
    no_page = 0
    login_wall = 0
    not_found = 0
    not_attempted = 0
    field_counts = {f: 0 for f in (
        "industry", "company_size", "founded_year", "description",
        "specialties", "followers", "website",
    )}
    baseline_scores: list[int] = []
    after_scores: list[int] = []
    gains: list[dict[str, Any]] = []

    for item in sample:
        cached = load_cached_company(item.company_name)
        record = (cached or {}).get("record") if cached else None
        baseline = _baseline_completeness(item)
        after = _linkedin_completeness(item, record)
        baseline_scores.append(baseline)
        after_scores.append(after)
        gain = after - baseline

        status = "not_attempted"
        error = None
        error_class = None

        if record:
            if record.get("scrape_status") == "ok":
                status = "linkedin_page_found"
                ok_count += 1
                for field in field_counts:
                    key = field
                    if field == "founded_year":
                        key = "founded"
                    val = record.get(field) or record.get(key)
                    if field == "followers":
                        val = _parse_followers(record)
                    if _populated(val):
                        field_counts[field] += 1
            else:
                error = record.get("scrape_error")
                error_class = _classify_error(error)
                if error_class == "not_found":
                    status = "not_found"
                    not_found += 1
                elif error_class == "login_wall":
                    status = "login_wall"
                    login_wall += 1
                else:
                    status = "failed"
                    no_page += 1
        else:
            not_attempted += 1

        if gain > 0:
            gains.append(
                {
                    "company_name": item.company_name,
                    "normalized_name_key": item.normalized_name_key,
                    "gain": gain,
                    "baseline_score": baseline,
                    "after_score": after,
                    "linkedin_url": item.linkedin_url,
                }
            )

        results.append(
            {
                "company_name": item.company_name,
                "normalized_name_key": item.normalized_name_key,
                "status": status,
                "error": error,
                "error_class": error_class,
                "baseline_completeness": baseline,
                "after_completeness": after,
                "gain": gain,
            }
        )

    attempted = sample_size - not_attempted
    success_rate = round(100.0 * ok_count / attempted, 1) if attempted else 0.0
    attempt_rate = round(100.0 * ok_count / sample_size, 1)
    projected_verified = int(round(total_pool * (ok_count / sample_size))) if sample_size else 0

    avg_before = round(sum(baseline_scores) / len(baseline_scores), 1) if baseline_scores else 0
    avg_after = round(sum(after_scores) / len(after_scores), 1) if after_scores else 0

    top_gains = sorted(gains, key=lambda x: (-x["gain"], -x["after_score"]))[:20]

    report = {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_validation_500",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "db_writes": False,
        "registry_integration": False,
        "auth_mode": resolve_auth_mode(),
        "profile_initialized": profile_is_initialized(),
        "sample_size": sample_size,
        "total_bc_pool": total_pool,
        "metrics": {
            "success_rate_attempted_pct": success_rate,
            "success_rate_sample_pct": attempt_rate,
            "linkedin_pages_found": ok_count,
            "no_linkedin_page": no_page + not_found,
            "not_attempted": not_attempted,
            "login_wall_or_blocked": login_wall,
            "not_found_or_invalid_url": not_found,
            "other_failures": no_page,
            "avg_completeness_before": avg_before,
            "avg_completeness_after": avg_after,
            "avg_completeness_gain": round(avg_after - avg_before, 1),
            "projected_verified_at_same_rate": projected_verified,
            "projected_verified_pct_of_pool": round(100.0 * projected_verified / total_pool, 1) if total_pool else 0,
        },
        "new_fields_collected": field_counts,
        "top_20_enrichment_gains": top_gains,
        "company_results": results,
    }
    return report


def render_validation_500_md(report: dict[str, Any]) -> str:
    m = report.get("metrics") or {}
    fields = report.get("new_fields_collected") or {}
    lines = [
        "# LinkedIn Validation — First 500 Companies",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Auth mode: **{report.get('auth_mode')}** | Profile initialized: **{report.get('profile_initialized')}**",
        f"Sample: **{report.get('sample_size')}** of **{report.get('total_bc_pool'):,}** BC construction pool",
        "",
    ]

    if not report.get("profile_initialized") and m.get("not_attempted", 0) == report.get("sample_size"):
        lines.extend(
            [
                "> **Blocked:** No persistent browser profile found. Run `python research/linkedin/scripts/login_profile.py` then re-run validation.",
                "",
            ]
        )

    lines.extend(
        [
            "## 1. Success rate",
            "",
            f"- **{m.get('success_rate_attempted_pct', 0)}%** of attempted scrapes succeeded",
            f"- **{m.get('success_rate_sample_pct', 0)}%** of sample ({m.get('linkedin_pages_found', 0)}/{report.get('sample_size')}) have verified LinkedIn pages",
            "",
            "## 2. Outcome breakdown",
            "",
            "| Outcome | Count |",
            "|---------|------:|",
            f"| LinkedIn pages found (scrape OK) | {m.get('linkedin_pages_found', 0)} |",
            f"| No LinkedIn page (other failure) | {m.get('other_failures', 0)} |",
            f"| Login wall / blocked | {m.get('login_wall_or_blocked', 0)} |",
            f"| 404 / invalid URL | {m.get('not_found_or_invalid_url', 0)} |",
            f"| Not yet attempted | {m.get('not_attempted', 0)} |",
            "",
            "## 3. New fields collected (verified pages only)",
            "",
            "| Field | Count |",
            "|-------|------:|",
        ]
    )
    for field, count in fields.items():
        lines.append(f"| {field} | {count} |")

    lines.extend(
        [
            "",
            "## 4. Profile completeness",
            "",
            f"- Average **before** LinkedIn enrichment: **{m.get('avg_completeness_before')}** / 100",
            f"- Average **after** LinkedIn enrichment: **{m.get('avg_completeness_after')}** / 100",
            f"- Average gain: **+{m.get('avg_completeness_gain')}**",
            "",
            "## 5. Top 20 enrichment gains",
            "",
        ]
    )
    top = report.get("top_20_enrichment_gains") or []
    if top:
        lines.append("| Company | Gain | Before → After |")
        lines.append("|---------|-----:|----------------|")
        for item in top:
            lines.append(
                f"| {item['company_name']} | +{item['gain']} | "
                f"{item['baseline_score']} → {item['after_score']} |"
            )
    else:
        lines.append("_No enrichment gains recorded yet._")

    lines.extend(
        [
            "",
            "## 6. Projected full-pool coverage",
            "",
            f"At the same success rate ({m.get('success_rate_sample_pct')}% of sample), estimated verified LinkedIn pages across the full pool:",
            "",
            f"- **~{m.get('projected_verified_at_same_rate'):,}** companies ({m.get('projected_verified_pct_of_pool')}% of {report.get('total_bc_pool'):,})",
            "",
            "## Safety",
            "",
            "- Read-only research under `research/linkedin/`",
            "- No Registry Engine integration",
            "- No production database writes",
            "",
        ]
    )
    return "\n".join(lines)


def write_validation_500_report(
    report: dict[str, Any],
    *,
    json_path=VALIDATION_500_JSON,
    md_path=VALIDATION_500_MD,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_validation_500_md(report), encoding="utf-8")
