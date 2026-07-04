"""Compare BD recommendation quality across engine iterations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401
from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from pipeline.bd_recommendations import recommend_bd_intelligence

VALIDATION_COHORT: list[tuple[int, str, str]] = [
    (165, "construction", "General Contractor"),
    (6999, "construction", "General Contractor"),
    (22, "construction", "General Contractor"),
    (670, "construction", "General Contractor"),
    (9444, "construction", "Concrete Contractor"),
    (4443, "construction", "Concrete Contractor"),
    (207, "construction", "Electrical Contractor"),
    (1310, "construction", "Electrical Contractor"),
    (839, "construction", "Mechanical Contractor"),
    (2951, "construction", "Mechanical Contractor"),
    (126, "architecture", "Architecture Firm"),
    (2, "architecture", "Architecture Firm"),
    (9, "architecture", "Architecture Firm"),
    (6, "architecture", "Architecture Firm"),
    (1735, "construction", "Engineering Consultant"),
    (2539, "construction", "Engineering Consultant"),
    (2070, "construction", "Engineering Consultant"),
    (134107, "construction", "Engineering Consultant"),
    (4060, "construction", "Engineering Consultant"),
    (370, "construction", "General Contractor"),
]

SECTION_KEYS = (
    "active_opportunities",
    "market_pipeline",
    "competitive_intelligence",
    "relationship_opportunities",
    "growth_opportunities",
)

# 10 representative companies for detailed validation report
DETAIL_COHORT = [165, 207, 126, 1735, 2070, 839, 9444, 370, 2, 670]


def _total_shown(result: dict) -> int:
    return sum(len(result.get(key, {}).get("items", [])) for key in SECTION_KEYS)


def _active_count(result: dict) -> int:
    return len(result.get("active_opportunities", {}).get("items", []))


def _opp_key(item: dict) -> str:
    payload = item.get("payload") or {}
    title = payload.get("title") or item.get("title") or ""
    oid = payload.get("id") or item.get("source_id") or item.get("id") or ""
    subtype = item.get("subtype") or item.get("item_type") or ""
    return f"{subtype}:{oid}:{title[:80]}"


def _collect_shown_items(full: dict) -> list[dict]:
    items: list[dict] = []
    for section in SECTION_KEYS:
        for item in full.get(section, {}).get("items", []):
            items.append({**item, "_section": section})
    return items


def _bps_scores(full: dict) -> list[int]:
    scores: list[int] = []
    for item in _collect_shown_items(full):
        expl = item.get("explanation") or {}
        bps = expl.get("bps") or item.get("score")
        if bps is not None:
            scores.append(int(bps))
    return scores


def _bps_distribution(scores: list[int]) -> dict[str, int]:
    buckets = {"65-69": 0, "70-79": 0, "80-89": 0, "90+": 0, "<65": 0}
    for s in scores:
        if s < 65:
            buckets["<65"] += 1
        elif s < 70:
            buckets["65-69"] += 1
        elif s < 80:
            buckets["70-79"] += 1
        elif s < 90:
            buckets["80-89"] += 1
        else:
            buckets["90+"] += 1
    return buckets


def audit_cohort(session, *, refresh: bool, include_rejections: bool) -> list[dict]:
    rows: list[dict] = []
    for cid, kind, category in VALIDATION_COHORT:
        print(f"  Auditing {cid} ({category})...", flush=True)
        r = recommend_bd_intelligence(
            session,
            company_id=cid,
            kind=kind,
            refresh_profile=refresh,
            include_rejections=include_rejections,
        )
        profile = r.get("company_intelligence_profile") or r.get("capability_profile", {})
        bps = _bps_scores(r)
        rows.append(
            {
                "company_id": cid,
                "category": category,
                "kind": kind,
                "primary_trade": profile.get("primary_trade"),
                "dominant_sector": profile.get("dominant_sector"),
                "work_orientation": profile.get("work_orientation"),
                "specialization_confidence": profile.get("specialization_confidence"),
                "total_shown": _total_shown(r),
                "active_shown": _active_count(r),
                "pipeline_shown": len(r.get("market_pipeline", {}).get("items", [])),
                "intel_shown": len(r.get("competitive_intelligence", {}).get("items", [])),
                "growth_shown": len(r.get("growth_opportunities", {}).get("items", [])),
                "gate_rejected_active": r.get("active_opportunities", {}).get("total_gate_rejected", 0),
                "top_active": [
                    i.get("payload", {}).get("title", i.get("title", ""))[:60]
                    for i in r.get("active_opportunities", {}).get("items", [])[:3]
                ],
                "engine_version": r.get("engine_version", "legacy"),
                "bps_scores": bps,
                "bps_distribution": _bps_distribution(bps),
                "shown_keys": [_opp_key(i) for i in _collect_shown_items(r)],
                "full": r if include_rejections else None,
            }
        )
    return rows


def summarize(label: str, rows: list[dict]) -> dict:
    n = len(rows)
    all_bps = [s for r in rows for s in r.get("bps_scores", [])]
    return {
        "label": label,
        "companies": n,
        "avg_total_shown": round(sum(r["total_shown"] for r in rows) / n, 1),
        "avg_active_shown": round(sum(r["active_shown"] for r in rows) / n, 1),
        "companies_with_zero": sum(1 for r in rows if r["total_shown"] == 0),
        "companies_with_active": sum(1 for r in rows if r["active_shown"] > 0),
        "companies_over_20": sum(1 for r in rows if r["total_shown"] > 20),
        "max_shown": max(r["total_shown"] for r in rows),
        "avg_bps": round(sum(all_bps) / len(all_bps), 1) if all_bps else 0,
        "bps_distribution": _bps_distribution(all_bps),
    }


def compare_runs(before_rows: list[dict], after_rows: list[dict]) -> dict:
    before_by_id = {r["company_id"]: r for r in before_rows}
    after_by_id = {r["company_id"]: r for r in after_rows}
    per_company: list[dict] = []

    total_fp = 0
    total_fn = 0

    for cid, _, category in VALIDATION_COHORT:
        b = before_by_id.get(cid, {})
        a = after_by_id.get(cid, {})
        before_keys = set(b.get("shown_keys", []))
        after_keys = set(a.get("shown_keys", []))
        fp_removed = sorted(before_keys - after_keys)
        fn_recovered = sorted(after_keys - before_keys)
        total_fp += len(fp_removed)
        total_fn += len(fn_recovered)
        per_company.append(
            {
                "company_id": cid,
                "category": category,
                "before_total": b.get("total_shown", 0),
                "after_total": a.get("total_shown", 0),
                "before_active": b.get("active_shown", 0),
                "after_active": a.get("active_shown", 0),
                "false_positives_removed": len(fp_removed),
                "false_negatives_recovered": len(fn_recovered),
                "fp_titles": fp_removed[:5],
                "fn_titles": fn_recovered[:5],
                "final_recommendation_count": a.get("total_shown", 0),
                "bps_distribution": a.get("bps_distribution", {}),
            }
        )

    return {
        "per_company": per_company,
        "totals": {
            "false_positives_removed": total_fp,
            "false_negatives_recovered": total_fn,
        },
        "before_summary": summarize("before", before_rows),
        "after_summary": summarize("after", after_rows),
    }


def _format_recommendation(item: dict) -> dict:
    payload = item.get("payload") or {}
    expl = item.get("explanation") or {}
    fits = expl.get("fit_breakdown") or expl.get("dimensions") or {}
    return {
        "section": item.get("_section", ""),
        "title": payload.get("title") or item.get("title") or "",
        "organization": payload.get("organization") or payload.get("company") or "",
        "bps": expl.get("bps") or item.get("score"),
        "reasons": item.get("reasons") or [],
        "fit_breakdown": fits,
    }


def _format_rejection(item: dict) -> dict:
    return {
        "title": (item.get("payload") or {}).get("title") or item.get("title") or "",
        "rejection_code": item.get("rejection_code") or "",
        "rejection_detail": item.get("rejection_detail") or "",
        "bps": (item.get("explanation") or {}).get("bps"),
    }


def build_global_rankings(after_rows: list[dict]) -> dict[str, Any]:
    all_shown: list[dict] = []
    all_rejected: list[dict] = []

    for row in after_rows:
        full = row.get("full") or {}
        cid = row["company_id"]
        for item in _collect_shown_items(full):
            payload = item.get("payload") or {}
            expl = item.get("explanation") or {}
            all_shown.append(
                {
                    "company_id": cid,
                    "category": row["category"],
                    "section": item.get("_section", ""),
                    "title": payload.get("title") or item.get("title") or "",
                    "organization": payload.get("organization") or payload.get("company") or "",
                    "bps": expl.get("bps") or item.get("score") or 0,
                    "reasons": item.get("reasons") or [],
                }
            )
        for rej in full.get("rejections") or []:
            all_rejected.append(
                {
                    "company_id": cid,
                    "category": row["category"],
                    "title": rej.get("title") or "",
                    "rejection_code": rej.get("rejection_code") or "",
                    "rejection_detail": rej.get("rejection_detail") or "",
                    "section": rej.get("section") or "",
                }
            )

    top_shown = sorted(all_shown, key=lambda x: x["bps"], reverse=True)[:10]
    top_rejected = all_rejected[:10]
    return {"top_10_recommendations": top_shown, "top_10_rejections": top_rejected}


def build_detail_examples(after_rows: list[dict], *, limit: int = 10) -> list[dict]:
    by_id = {r["company_id"]: r for r in after_rows}
    examples: list[dict] = []
    for cid in DETAIL_COHORT[:limit]:
        row = by_id.get(cid)
        if not row or not row.get("full"):
            continue
        full = row["full"]
        profile = full.get("company_intelligence_profile") or full.get("capability_profile") or {}
        shown = _collect_shown_items(full)
        shown_sorted = sorted(
            shown,
            key=lambda i: (i.get("explanation") or {}).get("bps") or i.get("score") or 0,
            reverse=True,
        )
        rejections = [
            _format_rejection(r) for r in (full.get("rejections") or [])[:8]
        ]

        examples.append(
            {
                "company_id": cid,
                "category": row["category"],
                "company_intelligence_profile": profile,
                "top_recommendations": [_format_recommendation(i) for i in shown_sorted[:5]],
                "sample_rejections": rejections[:8],
                "totals": {
                    "shown": row["total_shown"],
                    "active": row["active_shown"],
                    "pipeline": row["pipeline_shown"],
                    "intel": row["intel_shown"],
                },
            }
        )
    return examples


def main() -> int:
    guard_readonly_db(_SCRIPT)
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="bd-quality-audit.json")
    parser.add_argument("--before", default="bd-quality-audit-v3.json", help="Pre-tuning run (Iteration B)")
    parser.add_argument("--output", default="bd-quality-audit-v4.json")
    parser.add_argument("--comparison", default="bd-quality-comparison-v4.json")
    parser.add_argument("--examples", default="bd-quality-examples-v4.json")
    parser.add_argument("--skip-run", action="store_true", help="Only compare existing files")
    args = parser.parse_args()

    before_path = ROOT / args.before
    before_rows: list[dict] = []
    if before_path.exists():
        before_rows = json.loads(before_path.read_text(encoding="utf-8"))
        print(f"Loaded before-tuning results: {before_path} ({len(before_rows)} companies)")

    if args.skip_run and before_rows:
        after_rows = json.loads((ROOT / args.output).read_text(encoding="utf-8"))
    else:
        session = get_session()
        try:
            print("Running Iteration C audit (refresh profiles)...", flush=True)
            after_rows = audit_cohort(session, refresh=True, include_rejections=True)
        finally:
            session.close()

        out = ROOT / args.output
        out.write_text(json.dumps(after_rows, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {out}")

    comparison = compare_runs(before_rows, after_rows) if before_rows else {"after_summary": summarize("v3", after_rows)}

    baseline_path = ROOT / args.baseline
    if baseline_path.exists() and before_rows:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_rows = []
        for d in baseline:
            active_only = sum(
                1 for t in d.get("top_10_recommendations", []) if t.get("section") == "Active Opportunities"
            )
            baseline_rows.append(
                {
                    "company_id": d["company_id"],
                    "total_shown": d.get("statistics", {}).get("shown_total", 0),
                    "active_shown": active_only,
                }
            )
        comparison["phase1_keyword_summary"] = summarize("phase1_keyword", baseline_rows)

    examples = build_detail_examples(after_rows)
    examples_path = ROOT / args.examples
    examples_path.write_text(json.dumps(examples, indent=2, default=str), encoding="utf-8")
    comparison["detail_examples_count"] = len(examples)
    comparison["global_rankings"] = build_global_rankings(after_rows)

    comp_path = ROOT / args.comparison
    comp_path.write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {comp_path}")
    print(f"Wrote {examples_path}")
    print(json.dumps(comparison.get("before_summary", {}), indent=2))
    print(json.dumps(comparison.get("after_summary", {}), indent=2))
    if "totals" in comparison:
        print("Delta totals:", json.dumps(comparison["totals"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
