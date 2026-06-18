"""Validate Awards benchmark before/after deploy for 10 construction companies."""
from __future__ import annotations

import json
import sys
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

COMPANIES = [
    6999,   # Heatherbrae
    190,    # Sasco
    9460,   # Ventana
    1735,   # GHL
    2070,   # WSP (DBA profile)
    561,    # WSP (award-linked row)
    1921,   # LMDG
    670,    # Fusion
    165,    # Reotech
    799,    # Matra
]


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def fetch_company_index() -> dict[int, dict]:
    rows: dict[int, dict] = {}
    offset = 0
    while True:
        page = get(f"/api/companies?limit=500&offset={offset}")
        for company in page["data"]:
            rows[int(company["id"])] = company
        if len(page["data"]) < 500:
            break
        offset += 500
    return rows


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    companies = fetch_company_index()
    print(f"=== Awards benchmark {label} ===")
    print(f"{'ID':<7} {'Name':<38} {'DB cnt':>7} {'You':>5} {'Mkt':>6} {'Rival':>7}")
    print("-" * 75)
    for cid in COMPANIES:
        company = companies.get(cid)
        if not company:
            page = get(f"/api/companies?search=WSP&limit=20")
            company = next((c for c in page.get("data", []) if c.get("id") == cid), None)
        if not company and cid == 561:
            company = {"id": 561, "name": "Alex Olaru DBA: WSP Canada Inc", "award_count": "?"}
        if not company:
            print(f"{cid:<7} NOT FOUND")
            continue
        ci = get(f"/api/companies/{cid}/competitive-intelligence?peer_limit=5")
        metric = next(m for m in ci["benchmark"]["metrics"] if m["key"] == "award_count")
        name = str(company.get("name", ""))[:38]
        db_count = company.get("award_count", "?")
        print(
            f"{cid:<7} {name:<38} {str(db_count):>7} "
            f"{str(metric.get('company')):>5} {str(metric.get('market_median')):>6} "
            f"{str(metric.get('top_competitor_median')):>7}"
        )


if __name__ == "__main__":
    main()
