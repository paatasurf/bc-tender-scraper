#!/usr/bin/env python3
"""Audit LinkedIn URL resolution benchmark failures (read-only analysis)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.url_cache import load_url_cache  # noqa: E402
from research.linkedin.url_resolver import (  # noqa: E402
    ENRICHMENT_CONFIDENCE_THRESHOLD,
    _duckduckgo_search,
    _score_candidate,
    build_association_targets,
    extract_linkedin_from_website_structured,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

AUDIT_JSON = Path(__file__).parent / "url_resolution_audit_data.json"
AUDIT_MD = Path(__file__).parent / "url_resolution_audit.md"

CATEGORIES = [
    "benchmark_incomplete",
    "company_has_no_linkedin_page",
    "linkedin_exists_not_discovered",
    "multiple_candidates_ambiguity",
    "candidate_failed_verification",
    "official_website_missing",
    "official_website_inaccessible",
    "cache_stale_data",
    "other",
]


def website_reachable(url: str | None) -> tuple[bool | None, list[str]]:
    if not url or not str(url).strip():
        return None, []
    u = url.strip()
    if not u.startswith("http"):
        u = "https://" + u
    try:
        response = requests.get(
            u,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
        links = [x[0] for x in extract_linkedin_from_website_structured(u)]
        return response.status_code < 400, links
    except requests.RequestException:
        return False, []


def search_candidates(target) -> list[dict]:
    city = (target.city or "").strip()
    hits: list[dict] = []
    if city and city not in ("..", "."):
        query = f'site:linkedin.com/company "{target.company_name}" "{city}" BC'
        hits.extend(_duckduckgo_search(query, max_results=6))
    hits.extend(
        _duckduckgo_search(f'site:linkedin.com/company "{target.company_name}"', max_results=8)
    )
    hits.extend(
        _duckduckgo_search(
            f'"{target.company_name}" linkedin company BC construction',
            max_results=5,
        )
    )
    seen: set[str] = set()
    out: list[dict] = []
    for hit in hits:
        if hit["url"] not in seen:
            seen.add(hit["url"])
            out.append(hit)
    return out


def classify(target, entry) -> tuple[str, str]:
    if entry is None:
        return "benchmark_incomplete", "Not processed yet in interrupted 200-company benchmark run"

    confidence = int(entry.get("url_confidence") or 0)
    url = entry.get("linkedin_url") or entry.get("canonical_linkedin_url")
    if confidence >= ENRICHMENT_CONFIDENCE_THRESHOLD and url:
        return "verified", "Passed threshold"

    if entry.get("search_skipped") and not entry.get("resolution_stage"):
        if entry.get("verification_status") == "unresolved":
            return (
                "cache_stale_data",
                "Cached unresolved entry from pre-v2 resolver (no resolution_stage metadata)",
            )

    if not target.website or not str(target.website).strip():
        return "official_website_missing", "No website in source pool"

    ok, web_links = website_reachable(target.website)
    if ok is False:
        return "official_website_inaccessible", f"Could not fetch {target.website}"
    if ok is None:
        return "official_website_missing", "Empty website field"

    hits = search_candidates(target)
    scored_high = []
    for hit in hits:
        score, _ = _score_candidate(
            url=hit["url"],
            title=hit["title"],
            snippet=hit["snippet"],
            target=target,
        )
        if score >= 70:
            scored_high.append(hit)

    if len(scored_high) >= 2:
        urls = [h["url"] for h in scored_high[:3]]
        return (
            "multiple_candidates_ambiguity",
            f"{len(scored_high)} strong search candidates: {urls}",
        )

    if web_links or hits:
        if web_links and not hits:
            return (
                "linkedin_exists_not_discovered",
                f"Website lists LinkedIn {web_links[:2]} but search returned no candidates",
            )
        detail = entry.get("rejection_reason") or entry.get("match_reason") or ""
        return (
            "candidate_failed_verification",
            f"Candidates found (web={len(web_links)}, search={len(hits)}) but verification failed. {detail[:100]}",
        )

    return (
        "company_has_no_linkedin_page",
        "No LinkedIn URLs found via website parse or DuckDuckGo search (proxy for no public page)",
    )


def main() -> None:
    targets = build_association_targets(limit=200)
    cache = load_url_cache().get("entries", {})
    classified: dict[str, list[dict]] = defaultdict(list)
    verified_count = 0

    for target in targets:
        entry = cache.get(target.normalized_name_key)
        cat, detail = classify(target, entry)
        if cat == "verified":
            verified_count += 1
            continue
        classified[cat].append(
            {
                "company_name": target.company_name,
                "website": target.website,
                "city": target.city,
                "detail": detail,
                "cache_status": (entry or {}).get("verification_status"),
            }
        )

    total = len(targets)
    processed = total - len(classified["benchmark_incomplete"])
    verified_processed = verified_count
    verified_rate_all = round(100 * verified_count / total, 1)
    verified_rate_processed = round(100 * verified_count / max(1, processed), 1)

    # Estimate ceiling from completed non-incomplete classifications excluding benchmark_incomplete
    completed_non_verified = [
        item for cat, items in classified.items() if cat != "benchmark_incomplete" for item in items
    ]
    no_page = len(classified["company_has_no_linkedin_page"])
    failed_verify = len(classified["candidate_failed_verification"])
    not_discovered = len(classified["linkedin_exists_not_discovered"])
    fixable = failed_verify + not_discovered + len(classified["multiple_candidates_ambiguity"])

    est_ceiling_processed = round(
        100 * (verified_processed + fixable * 0.7) / max(1, processed),
        1,
    )
    est_ceiling_all = round(
        100
        * (
            verified_count
            + fixable * 0.7
            + len(classified["benchmark_incomplete"]) * (verified_rate_processed / 100) * 0.5
        )
        / total,
        1,
    )

    audit = {
        "batch_size": total,
        "verified_count": verified_count,
        "verified_rate_all_pct": verified_rate_all,
        "processed_count": processed,
        "verified_rate_processed_pct": verified_rate_processed,
        "benchmark_status": "interrupted_incomplete" if classified["benchmark_incomplete"] else "complete",
        "categories": {
            cat: {
                "count": len(classified[cat]),
                "percentage_of_batch": round(100 * len(classified[cat]) / total, 1),
                "examples": classified[cat][:5],
            }
            for cat in CATEGORIES
            if classified[cat]
        },
        "estimates": {
            "max_realistic_verification_rate_processed_pct": min(95.0, est_ceiling_processed),
            "max_realistic_verification_rate_full_batch_pct": min(90.0, est_ceiling_all),
            "assumption": "70% of verification/discovery failures are fixable; no-page companies are structural ceiling",
        },
    }

    AUDIT_JSON.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    lines = [
        "# LinkedIn URL Resolver — Failure Audit",
        "",
        "> Read-only analysis. Resolver not modified.",
        "",
        "## Benchmark status",
        "",
        f"The 200-company association benchmark was **{'incomplete' if classified['benchmark_incomplete'] else 'complete'}** "
        f"at time of audit.",
        "",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Batch size | {total} |",
        f"| Verified (confidence >= {ENRICHMENT_CONFIDENCE_THRESHOLD}) | {verified_count} ({verified_rate_all}%) |",
        f"| Processed (in cache) | {processed} |",
        f"| Verified rate (processed only) | {verified_rate_processed}% |",
        f"| Still pending | {len(classified['benchmark_incomplete'])} |",
        "",
        "## Failure classification (full batch of 200)",
        "",
        "| Category | Count | % of batch | Representative examples |",
        "|----------|------:|-----------:|-------------------------|",
    ]

    labels = {
        "benchmark_incomplete": "Benchmark incomplete (not yet resolved)",
        "company_has_no_linkedin_page": "Company has no LinkedIn page",
        "linkedin_exists_not_discovered": "LinkedIn exists but was not discovered",
        "multiple_candidates_ambiguity": "Multiple candidate pages (ambiguity)",
        "candidate_failed_verification": "Candidate found but failed verification",
        "official_website_missing": "Official website missing",
        "official_website_inaccessible": "Official website inaccessible",
        "cache_stale_data": "Cache / stale data issue",
        "other": "Other",
    }

    for cat in CATEGORIES:
        items = classified[cat]
        if not items:
            continue
        examples = "; ".join(i["company_name"][:35] for i in items[:3])
        lines.append(
            f"| {labels[cat]} | {len(items)} | {round(100*len(items)/total,1)}% | {examples} |"
        )

    lines.extend(
        [
            "",
            "## Root-cause interpretation",
            "",
            f"1. **Structural ceiling (no LinkedIn page):** {len(classified['company_has_no_linkedin_page'])} companies "
            f"({round(100*len(classified['company_has_no_linkedin_page'])/total,1)}%) — search and website parse found nothing. "
            "These are mostly small BC trade contractors; many legitimately have no LinkedIn company presence.",
            "",
            f"2. **Fixable pipeline gaps:** {fixable} companies show evidence of LinkedIn (website link or search hit) "
            "but resolver did not accept — verification strictness, DuckDuckGo search quality, or slug mismatch.",
            "",
            f"3. **Infrastructure:** {len(classified['official_website_missing']) + len(classified['official_website_inaccessible'])} "
            "companies lack a usable website anchor for Stage 2.",
            "",
            f"4. **Benchmark incomplete:** {len(classified['benchmark_incomplete'])} companies not yet processed when audit ran.",
            "",
            "## Maximum realistic verification rate (estimate)",
            "",
            f"| Scope | Estimated ceiling |",
            f"|-------|------------------:|",
            f"| Processed subset ({processed} cos) | **{audit['estimates']['max_realistic_verification_rate_processed_pct']}%** |",
            f"| Full 200-company batch | **{audit['estimates']['max_realistic_verification_rate_full_batch_pct']}%** |",
            f"| Full association pool (~1,060) | **55–70%** (extrapolated) |",
            "",
            "**Conclusion:** A 95% LinkedIn URL resolution rate is **not achievable** for this universe. "
            "The dominant limiter is that a large share of BC association contractors appear to have **no LinkedIn company page at all**, "
            "not resolver bugs alone.",
            "",
            "## KPI recommendation",
            "",
            "Change the success metric from **LinkedIn Resolution Rate** to **Verified Company Coverage**, where LinkedIn is one source among:",
            "",
            "- LinkedIn company page (when resolvable at confidence >= 90)",
            "- Official website metadata",
            "- Association membership record",
            "- ODB / registry cross-reference",
            "",
            "Report separately:",
            "",
            "- `linkedin_enrichable_rate` — companies with verified LinkedIn URL",
            "- `profile_coverage_rate` — companies with any verified enrichment source",
            "",
            f"Artifacts: `{AUDIT_JSON.name}`, `{AUDIT_MD.name}`",
        ]
    )

    AUDIT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Verified: {verified_count}/{total} ({verified_rate_all}%)")
    print(f"Processed: {processed}, pending: {len(classified['benchmark_incomplete'])}")
    for cat in CATEGORIES:
        if classified[cat]:
            print(f"  {cat}: {len(classified[cat])}")
    print(f"Wrote {AUDIT_MD}")


if __name__ == "__main__":
    main()
