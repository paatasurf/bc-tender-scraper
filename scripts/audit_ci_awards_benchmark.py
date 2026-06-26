"""Audit Awards metric in Competitive Intelligence benchmark (read-only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter

API = "https://bc-tender-scraper-production.up.railway.app"

SAMPLE_IDS = [
    1921,  # LMDG
    670,   # Fusion Projects
    1735,  # GHL
    134635,  # Simex Defence (award-heavy)
    165,   # Reotech
    9444,  # Concrete Cashmere
    42,    # small GC
    6999,  # Heatherbrae
    1920,
    100,
]


def get(path: str) -> dict | list:
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def fetch_all_companies_award_stats(max_pages: int = 50, page_size: int = 500) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    for _ in range(max_pages):
        page = get(f"/api/companies?limit={page_size}&offset={offset}")
        batch = page.get("data", [])
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def main() -> None:
    print("=== 1. companies.award_count distribution (production API) ===")
    companies = fetch_all_companies_award_stats()
    total = len(companies)
    nonzero = [c for c in companies if int(c.get("award_count") or 0) > 0]
    zero = total - len(nonzero)
    print(f"Companies fetched: {total}")
    print(f"award_count > 0: {len(nonzero)} ({100*len(nonzero)/total:.1f}%)" if total else "n/a")
    print(f"award_count = 0: {zero}")
    if nonzero:
        top = sorted(nonzero, key=lambda c: int(c.get("award_count") or 0), reverse=True)[:10]
        print("Top 10 by award_count:")
        for c in top:
            print(
                f"  id={c['id']} count={c.get('award_count')} "
                f"total_award_value={c.get('total_award_value')} "
                f"name={c.get('name','')[:50]}"
            )

    print("\n=== 2. contract_awards company_id matching ===")
    summary = get("/api/contract-awards/summary")
    print(json.dumps(summary, indent=2))
    matched_sample = get("/api/contract-awards?matched=true&limit=5")
    unmatched_sample = get("/api/contract-awards?matched=false&limit=5")
    print("Matched sample company_id values:", [r.get("company_id") for r in matched_sample.get("data", [])])
    print("Unmatched sample company_id values:", [r.get("company_id") for r in unmatched_sample.get("data", [])])

    print("\n=== 3. benchmark.py field mapping check ===")
    print("benchmark reads companies.award_count via getattr(company, 'award_count')")
    print("Does NOT read total_award_value — Awards metric is count only")

    print("\n=== 4. Ten construction companies: DB vs benchmark API ===")
    print(f"{'ID':<8} {'Name':<42} {'DB award_count':<14} {'DB total_award_value':<20} {'Bench You':<10} {'Mkt Med':<8} {'Rival Med':<10}")
    print("-" * 120)
    for cid in SAMPLE_IDS:
        company = next((c for c in companies if c.get("id") == cid), None)
        if not company:
            print(f"{cid:<8} company row not found in companies list")
            continue

        name = str(company.get("name", ""))[:42]
        db_count = int(company.get("award_count") or 0)
        db_value = float(company.get("total_award_value") or 0)

        try:
            ci = get(f"/api/companies/{cid}/competitive-intelligence?peer_limit=5")
        except urllib.error.HTTPError as exc:
            print(f"{cid:<8} {name:<42} CI HTTP {exc.code}")
            continue

        awards = next((m for m in ci.get("benchmark", {}).get("metrics", []) if m.get("key") == "award_count"), {})
        bench_you = awards.get("company")
        mkt = awards.get("market_median")
        rival = awards.get("top_competitor_median")
        print(
            f"{cid:<8} {name:<42} {db_count:<14} {db_value:<20.0f} "
            f"{str(bench_you):<10} {str(mkt):<8} {str(rival):<10}"
        )

    print("\n=== 5. Cohort award_count for LMDG (1921) ===")
    ci1921 = get("/api/companies/1921/competitive-intelligence?peer_limit=5")
    cohort_size = ci1921.get("market", {}).get("cohort_size")
    peer_award_counts = [p.get("award_count") for p in ci1921.get("top_competitors", [])]
    print(f"cohort_size={cohort_size} top_competitor award_counts={peer_award_counts}")
    awards_metric = next(m for m in ci1921["benchmark"]["metrics"] if m["key"] == "award_count")
    print("LMDG benchmark awards metric:", json.dumps(awards_metric))


if __name__ == "__main__":
    main()
