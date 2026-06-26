"""Final 10-company CI awards table."""
from __future__ import annotations

import json
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

# Mix: permit-heavy GCs (often 0 awards) + companies with known awards
IDS = [
    (1921, "LMDG (code consultant, permit-heavy)"),
    (670, "Fusion Projects (GC)"),
    (165, "Reotech Construction"),
    (6999, "Heatherbrae Builders"),
    (1735, "GHL Consultants"),
    (190, "Sasco Contractors"),
    (9460, "Ventana Construction"),
    (799, "Matra Construction Inc"),
    (2070, "WSP Canada (eng/consulting)"),
    (134635, "Simex Defence (award-only)"),
]


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def fetch_companies():
    rows, offset = [], 0
    while True:
        page = get(f"/api/companies?limit=500&offset={offset}")
        batch = page["data"]
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return {c["id"]: c for c in rows}


def main():
    by_id = fetch_companies()
    print(f"{'Company':<48} {'award_count':>11} {'total_award_value':>18} {'You':>5} {'MktMed':>7} {'RivalMed':>9}")
    print("-" * 105)
    for cid, label in IDS:
        c = by_id.get(cid)
        if not c:
            print(f"{label}: missing")
            continue
        ci = get(f"/api/companies/{cid}/competitive-intelligence?peer_limit=5")
        m = next(x for x in ci["benchmark"]["metrics"] if x["key"] == "award_count")
        name = str(c.get("name", label))[:48]
        print(
            f"{name:<48} {int(c.get('award_count') or 0):>11} "
            f"{float(c.get('total_award_value') or 0):>18,.0f} "
            f"{str(m.get('company')):>5} {str(m.get('market_median')):>7} {str(m.get('top_competitor_median')):>9}"
        )


if __name__ == "__main__":
    main()
