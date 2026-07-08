"""Validation report builder — multi-source comparison + quality audit."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from research.linkedin.compare import _discovery_confidence
from research.linkedin.paths import (
    HIGH_CONFIDENCE_NEW_JSON,
    VALIDATION_JSON,
    VALIDATION_MD,
)
from research.linkedin.quality_audit import audit_top_new_companies, is_bc_headquarters
from research.linkedin.sources_index import SourceIndex, load_combined_source_index


def compare_multi_source(
    normalized: dict[str, Any],
    *,
    source_index: SourceIndex | None = None,
) -> dict[str, Any]:
    source_index = source_index or load_combined_source_index()
    already_known: list[dict[str, Any]] = []
    potentially_new: list[dict[str, Any]] = []
    possible_duplicates: list[dict[str, Any]] = []

    by_key: dict[str, list[dict[str, Any]]] = {}
    for rec in normalized.get("records") or []:
        key = rec.get("normalized_name_key") or ""
        by_key.setdefault(key, []).append(rec)

    for key, group in by_key.items():
        if key and len(group) > 1:
            possible_duplicates.append(
                {
                    "normalized_name_key": key,
                    "count": len(group),
                    "company_names": [g.get("company_name") for g in group],
                    "linkedin_urls": [g.get("linkedin_company_url") for g in group],
                }
            )

    seen_known: set[str] = set()
    seen_new: set[str] = set()
    core_sources = {"enterprise_seed", "odbus_bc_naics23", "market_registry_baseline"}
    for rec in normalized.get("records") or []:
        key = rec.get("normalized_name_key") or ""
        if key and source_index.is_known(key):
            if key in seen_known:
                continue
            seen_known.add(key)
            sources = source_index.sources_for(key)
            already_known.append(
                {
                    "company_name": rec.get("company_name"),
                    "normalized_name_key": key,
                    "linkedin_company_url": rec.get("linkedin_company_url"),
                    "matched_sources": sources,
                    "known_in_core_registry": any(s in core_sources for s in sources),
                    "known_in_associations_only": all(s.startswith("association_") for s in sources),
                    "linkedin_page_verified": rec.get("linkedin_page_verified"),
                    "registry_display_name": source_index.display_names.get(key),
                }
            )
        elif key and key not in seen_new:
            seen_new.add(key)
            potentially_new.append(
                {
                    **rec,
                    "match_status": "not_in_any_local_snapshot",
                    "discovery_confidence": _discovery_confidence(rec),
                    "bc_headquarters": is_bc_headquarters(rec),
                    "has_website": bool((rec.get("website") or "").strip()),
                }
            )
        elif not key:
            potentially_new.append(
                {
                    **rec,
                    "match_status": "unmatched_no_name_key",
                    "discovery_confidence": _discovery_confidence(rec),
                    "bc_headquarters": is_bc_headquarters(rec),
                    "has_website": bool((rec.get("website") or "").strip()),
                }
            )

    potentially_new.sort(key=lambda r: (-r.get("discovery_confidence", 0), r.get("company_name") or ""))

    with_website = sum(1 for r in potentially_new if r.get("has_website"))
    without_website = len(potentially_new) - with_website
    bc_hq = sum(1 for r in potentially_new if r.get("bc_headquarters"))
    linkedin_verified = sum(1 for r in normalized.get("records") or [] if r.get("linkedin_page_verified"))
    known_core_only = sum(1 for r in already_known if r.get("known_in_core_registry"))
    known_assoc_only = sum(
        1
        for r in already_known
        if r.get("known_in_associations_only") or any(
            s.startswith("association_") for s in (r.get("matched_sources") or [])
        )
    )

    match_by_source: dict[str, int] = {}
    for row in already_known:
        for src in row.get("matched_sources") or []:
            match_by_source[src] = match_by_source.get(src, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_index_stats": source_index.stats,
        "linkedin_record_count": normalized.get("record_count", 0),
        "already_known_count": len(already_known),
        "potentially_new_count": len(potentially_new),
        "possible_duplicates_count": len(possible_duplicates),
        "potentially_new_with_website": with_website,
        "potentially_new_without_website": without_website,
        "potentially_new_bc_headquarters": bc_hq,
        "linkedin_pages_verified": linkedin_verified,
        "already_known_in_core_registry": known_core_only,
        "already_known_via_associations": known_assoc_only,
        "already_known_by_source": match_by_source,
        "already_known": already_known,
        "potentially_new": potentially_new,
        "possible_duplicates": possible_duplicates,
    }


def build_validation_report(
    *,
    raw: dict[str, Any],
    normalized: dict[str, Any],
    comparison: dict[str, Any],
    candidates_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    top_new = (comparison.get("potentially_new") or [])[:100]
    quality = audit_top_new_companies(top_new)
    high_confidence = [
        {
            "company_name": r.get("company_name"),
            "normalized_name_key": r.get("normalized_name_key"),
            "linkedin_company_url": r.get("linkedin_company_url"),
            "website": r.get("website"),
            "industry": r.get("industry"),
            "headquarters": r.get("headquarters"),
            "description": (r.get("description") or "")[:300],
            "discovery_confidence": r.get("discovery_confidence"),
            "bc_headquarters": r.get("bc_headquarters"),
            "primary_classification": r.get("primary_classification"),
            "construction_related": r.get("construction_related"),
            "likely_false_positive": r.get("likely_false_positive"),
        }
        for r in quality.get("records") or []
    ]

    return {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_validation_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "db_writes": False,
        "disclaimer": "Experimental research — no Registry integration, no DB writes.",
        "discovery": {
            "mode": raw.get("mode"),
            "library": raw.get("library"),
            "fetch_method": raw.get("fetch_method"),
            "total_companies_collected": raw.get("record_count", 0),
            "scrape_ok": sum(1 for r in raw.get("records") or [] if r.get("scrape_status") == "ok"),
            "scrape_errors": sum(1 for r in raw.get("records") or [] if r.get("scrape_status") == "error"),
            "url_candidates": candidates_meta,
        },
        "summary": {
            "total_companies_collected": raw.get("record_count", 0),
            "linkedin_pages_verified": comparison.get("linkedin_pages_verified"),
            "already_known": comparison.get("already_known_count"),
            "already_known_in_core_registry": comparison.get("already_known_in_core_registry"),
            "already_known_via_associations": comparison.get("already_known_via_associations"),
            "potentially_new": comparison.get("potentially_new_count"),
            "possible_duplicates": comparison.get("possible_duplicates_count"),
            "companies_with_websites": comparison.get("potentially_new_with_website"),
            "companies_without_websites": comparison.get("potentially_new_without_website"),
            "companies_with_bc_headquarters": comparison.get("potentially_new_bc_headquarters"),
        },
        "comparison_sources": {
            "enterprise_seed": comparison.get("source_index_stats", {}).get("enterprise_seed_keys"),
            "odbus_bc_naics23": comparison.get("source_index_stats", {}).get("odbus_keys"),
            "associations": comparison.get("source_index_stats", {}).get("association_keys"),
            "market_registry_baseline": comparison.get("source_index_stats", {}).get(
                "market_registry_baseline_keys"
            ),
            "combined_unique_keys": comparison.get("source_index_stats", {}).get("combined_unique_keys"),
            "already_known_by_source": comparison.get("already_known_by_source"),
        },
        "quality_audit_top_100_new": {
            "classification_counts": quality.get("classification_counts"),
            "likely_false_positives": quality.get("likely_false_positives"),
            "estimated_false_positive_rate_pct": quality.get("estimated_false_positive_rate_pct"),
        },
        "top_100_highest_confidence_new": high_confidence,
        "comparison": comparison,
        "quality_audit": quality,
    }


def render_validation_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    disc = report.get("discovery") or {}
    qa = report.get("quality_audit_top_100_new") or {}
    lines = [
        "# LinkedIn Discovery — Validation Report",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "> Research only. No database writes. No Registry integration.",
        "",
        "## Discovery",
        "",
        f"- Mode: `{disc.get('mode')}`",
        f"- Fetch method: `{disc.get('fetch_method')}`",
        f"- URL candidates: {((disc.get('url_candidates') or {}).get('candidate_count'))}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Total companies collected | {summary.get('total_companies_collected', 0)} |",
        f"| Scrape OK (LinkedIn verified) | {disc.get('scrape_ok', 0)} |",
        f"| LinkedIn pages verified | {summary.get('linkedin_pages_verified', 0)} |",
        f"| Already known (any source) | {summary.get('already_known', 0)} |",
        f"| Already known — core registry | {summary.get('already_known_in_core_registry', 0)} |",
        f"| Already known — associations | {summary.get('already_known_via_associations', 0)} |",
        f"| Potentially new (all sources) | {summary.get('potentially_new', 0)} |",
        f"| Possible duplicates | {summary.get('possible_duplicates', 0)} |",
        f"| New — with website | {summary.get('companies_with_websites', 0)} |",
        f"| New — without website | {summary.get('companies_without_websites', 0)} |",
        f"| New — BC headquarters | {summary.get('companies_with_bc_headquarters', 0)} |",
        "",
        "## Comparison sources (local snapshots)",
        "",
    ]
    cs = report.get("comparison_sources") or {}
    for label, key in (
        ("Enterprise Seed keys", "enterprise_seed"),
        ("ODB BC NAICS-23 keys", "odbus_bc_naics23"),
        ("Association keys", "associations"),
        ("Market Registry baseline keys", "market_registry_baseline"),
        ("Combined unique keys", "combined_unique_keys"),
    ):
        lines.append(f"- {label}: **{cs.get(key, 0)}**")
    lines.extend(["", "## Quality audit (Top 100 new)", ""])
    lines.append(f"- Estimated false-positive rate: **{qa.get('estimated_false_positive_rate_pct', 0)}%**")
    lines.append(f"- Likely false positives: **{qa.get('likely_false_positives', 0)}** / 100")
    lines.append("")
    lines.append("Classification breakdown:")
    for label, count in sorted((qa.get("classification_counts") or {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Top 100 highest-confidence NEW companies", ""])
    top = report.get("top_100_highest_confidence_new") or []
    if not top:
        lines.append("_None._")
    else:
        lines.append("| Conf | Company | Class | BC HQ | Website | LinkedIn |")
        lines.append("|-----:|---------|-------|------:|---------|----------|")
        for row in top[:100]:
            lines.append(
                f"| {row.get('discovery_confidence', '')} "
                f"| {row.get('company_name') or '—'} "
                f"| {row.get('primary_classification', '')} "
                f"| {'yes' if row.get('bc_headquarters') else 'no'} "
                f"| {row.get('website') or '—'} "
                f"| {row.get('linkedin_company_url') or '—'} |"
            )
    return "\n".join(lines) + "\n"


def write_validation_outputs(report: dict[str, Any]) -> tuple[Any, ...]:
    VALIDATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    VALIDATION_MD.write_text(render_validation_markdown(report), encoding="utf-8")
    high_conf = {
        "generated_at": report.get("generated_at"),
        "count": len(report.get("top_100_highest_confidence_new") or []),
        "estimated_false_positive_rate_pct": (report.get("quality_audit_top_100_new") or {}).get(
            "estimated_false_positive_rate_pct"
        ),
        "companies": report.get("top_100_highest_confidence_new") or [],
    }
    HIGH_CONFIDENCE_NEW_JSON.write_text(json.dumps(high_conf, indent=2, default=str), encoding="utf-8")
    return VALIDATION_JSON, VALIDATION_MD, HIGH_CONFIDENCE_NEW_JSON
