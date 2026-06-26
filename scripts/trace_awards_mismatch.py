"""Find mismatch: contract_awards linked but companies.award_count=0."""
from __future__ import annotations

import json
import urllib.request
from collections import defaultdict

API = "https://bc-tender-scraper-production.up.railway.app"


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def main() -> None:
    # All awards grouped by company_id
    by_company: dict[int, int] = defaultdict(int)
    offset = 0
    while True:
        page = get(f"/api/contract-awards?limit=500&offset={offset}")
        for r in page["data"]:
            cid = r.get("company_id")
            if cid:
                by_company[int(cid)] += 1
        if len(page["data"]) < 500:
            break
        offset += 500

    print(f"Unique company_ids in contract_awards: {len(by_company)}")

    companies = {}
    offset = 0
    while True:
        page = get(f"/api/companies?limit=500&offset={offset}")
        for c in page["data"]:
            companies[c["id"]] = c
        if len(page["data"]) < 500:
            break
        offset += 500

    mismatches = []
    for cid, ca_count in sorted(by_company.items(), key=lambda x: -x[1])[:500]:
        co = companies.get(cid)
        db_count = int(co.get("award_count") or 0) if co else -1
        if ca_count > 0 and db_count != ca_count:
            mismatches.append((cid, ca_count, db_count, co.get("name", "?") if co else "MISSING"))

    print(f"Mismatches (CA count != companies.award_count): {len(mismatches)}")
    for row in mismatches[:20]:
        print(f"  id={row[0]} CA={row[1]} companies.award_count={row[2]} name={str(row[3])[:50]}")

    # Companies with permits, CA>0, award_count=0
    stale = [
        (cid, ca_count, companies[cid])
        for cid, ca_count in by_company.items()
        if cid in companies
        and ca_count > 0
        and int(companies[cid].get("award_count") or 0) == 0
        and int(companies[cid].get("total_projects") or 0) >= 5
    ]
    print(f"\nPermit-active (>=5 projects) with CA rows but award_count=0: {len(stale)}")
    for cid, ca_count, co in sorted(stale, key=lambda x: -x[1])[:10]:
        print(f"  id={cid} CA={ca_count} projects={co.get('total_projects')} {co.get('name','')[:50]}")


if __name__ == "__main__":
    main()
