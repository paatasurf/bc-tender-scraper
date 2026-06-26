"""Permit vs award overlap + name search on contract awards."""
from __future__ import annotations

import json
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def fetch_companies():
    rows = []
    offset = 0
    while True:
        page = get(f"/api/companies?limit=500&offset={offset}")
        batch = page["data"]
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return rows


def main():
    companies = fetch_companies()
    both = [c for c in companies if int(c.get("total_projects") or 0) >= 10 and int(c.get("award_count") or 0) > 0]
    permits_no = [c for c in companies if int(c.get("total_projects") or 0) >= 20 and int(c.get("award_count") or 0) == 0]
    print(f">=10 projects AND award_count>0: {len(both)}")
    print(f">=20 projects AND award_count=0: {len(permits_no)}")
    print("\nPermit-heavy, zero awards (top 8):")
    for c in sorted(permits_no, key=lambda x: -int(x.get("total_projects") or 0))[:8]:
        name = str(c.get("name", ""))[:55]
        print(f"  id={c['id']} projects={c.get('total_projects')} {name}")

    print("\nBoth permits + awards (top 10 by award_count):")
    for c in sorted(both, key=lambda x: -int(x.get("award_count") or 0))[:10]:
        name = str(c.get("name", ""))[:50]
        print(f"  id={c['id']} awards={c.get('award_count')} projects={c.get('total_projects')} {name}")

    print("\nTop vendors (contract_awards aggregation):")
    vendors = get("/api/contract-awards/top-vendors?limit=15")
    for v in vendors.get("data", []):
        print(f"  company_id={v.get('company_id')} count={v.get('award_count')} name={v.get('company_name','')[:45]}")

    # CI benchmark for a permit+award company
    for cid in [6999, 1735, 134635]:
        ci = get(f"/api/companies/{cid}/competitive-intelligence?peer_limit=5")
        m = next(x for x in ci["benchmark"]["metrics"] if x["key"] == "award_count")
        print(f"\nCI awards id={cid}: {m}")


if __name__ == "__main__":
    main()
