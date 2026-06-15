"""Audit production contract-awards endpoints (read-only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter

BASE = "https://bc-tender-scraper-production.up.railway.app"


def get(path: str, timeout: int = 90) -> dict | list | None:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"_error": exc.code, "_path": path, "_body": exc.read().decode()[:500]}


def main() -> None:
    print("=== ENDPOINT AVAILABILITY ===")
    for path in [
        "/api/contract-awards/summary",
        "/api/contract-awards/top-vendors?limit=10",
        "/api/stats",
    ]:
        result = get(path, timeout=30)
        if isinstance(result, dict) and "_error" in result:
            print(f"{path}: HTTP {result['_error']}")
        else:
            print(f"{path}: OK")
            print(json.dumps(result, indent=2)[:1200])
        print()

    print("=== PAGINATE /api/contract-awards ===")
    all_rows: list[dict] = []
    offset = 0
    limit = 100
    reported_totals: list[int] = []

    while True:
        page = get(f"/api/contract-awards?limit={limit}&offset={offset}")
        if not isinstance(page, dict) or "data" not in page:
            print("Unexpected page:", page)
            break
        rows = page.get("data", [])
        all_rows.extend(rows)
        reported_totals.append(int(page.get("total", 0)))
        print(
            f"offset={offset} returned={len(rows)} "
            f"reported_total={page.get('total')} keys={list(rows[0].keys()) if rows else []}"
        )
        if not rows or len(rows) < limit:
            break
        offset += limit
        if offset > 20_000:
            print("Stopped at safety cap 20k")
            break

    print(f"\nFetched rows: {len(all_rows)}")
    print(f"Reported totals per page: {reported_totals}")

    if not all_rows:
        print("No contract-award rows returned.")
        return

    keys = set(all_rows[0].keys())
    print(f"Schema keys: {sorted(keys)}")

    # Legacy vs new schema detection
    is_legacy = keys <= {"company", "contract", "value", "date"}
    print(f"Legacy schema (company/contract/value/date only): {is_legacy}")

    print("\n=== COVERAGE (legacy schema) ===")
    dates = [str(r.get("date", "")).strip() for r in all_rows]
    contracts = [str(r.get("contract", "")) for r in all_rows]
    companies = [str(r.get("company", "")).strip() for r in all_rows]
    values = [r.get("value") for r in all_rows]

    print(f"company nonempty: {sum(1 for x in companies if x)} / {len(companies)}")
    print(f"date nonempty: {sum(1 for x in dates if x)} / {len(dates)}")
    print(f"value > 0: {sum(1 for v in values if isinstance(v, (int, float)) and v > 0)} / {len(values)}")
    print(
        f"contract looks like permit rollup ('N permits'): "
        f"{sum(1 for c in contracts if 'permit' in c.lower())} / {len(contracts)}"
    )

    print("\n=== COVERAGE (Phase B fields — new schema) ===")
    for field in [
        "winner_company",
        "buyer_organization",
        "buyer_level",
        "award_date",
        "procurement_category",
        "source",
        "title",
    ]:
        present = sum(1 for r in all_rows if str(r.get(field, "")).strip())
        print(f"{field} nonempty: {present} / {len(all_rows)}")

    print("\n=== SAMPLE PAYLOADS ===")
    for label, row in [("first", all_rows[0]), ("middle", all_rows[len(all_rows) // 2]), ("last", all_rows[-1])]:
        print(f"\n--- {label} ---")
        print(json.dumps(row, indent=2))

    print("\n=== COMPANIES AWARD FIELDS (sample) ===")
    companies_page = get("/api/companies?limit=20&offset=0")
    if isinstance(companies_page, dict) and companies_page.get("data"):
        sample = companies_page["data"][0]
        print("Total companies:", companies_page.get("total"))
        print("Sample keys:", sorted(sample.keys()))
        award_fields = [
            "award_count",
            "total_award_value",
            "avg_award_value",
            "award_clients",
            "award_categories",
            "buyer_levels",
            "award_sources",
            "data_sources",
        ]
        for field in award_fields:
            in_schema = field in sample
            print(f"  {field}: in_schema={in_schema} sample_value={sample.get(field)!r}")


if __name__ == "__main__":
    main()
