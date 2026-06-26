"""End-to-end Awards trace: DB (API) vs CI API for 5 construction companies."""
from __future__ import annotations

import json
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

# Construction companies with known award activity
IDS = [6999, 190, 9460, 1735, 2070, 1921, 670, 165]


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def award_rows_for_company(company_id: int) -> tuple[int, float]:
    page = get(f"/api/contract-awards?company_id={company_id}&limit=500")
    rows = page.get("data", [])
    total = int(page.get("total", len(rows)))
    value = sum(float(r.get("award_value") or 0) for r in rows)
    return total, value


def main() -> None:
    companies = {}
    offset = 0
    while True:
        page = get(f"/api/companies?limit=500&offset={offset}")
        for c in page["data"]:
            companies[c["id"]] = c
        if len(page["data"]) < 500:
            break
        offset += 500

    print(
        f"{'ID':<7} {'Name':<40} {'CA rows':>8} {'CA value':>14} "
        f"{'co.award_count':>14} {'co.total_award':>14} {'CI You':>7} {'Mkt':>6} {'Rival':>7}"
    )
    print("-" * 130)

    for cid in IDS:
        c = companies.get(cid)
        if not c:
            c = get(f"/api/companies?search={cid}&limit=1").get("data", [{}])[0]
            if c.get("id") != cid:
                # search by id won't work; fetch via search name from known list
                pass
        if not c or c.get("id") != cid:
            for needle in ["Heatherbrae", "Sasco", "Ventana", "GHL", "WSP", "LMDG", "Fusion", "Reotech"]:
                pass
            c = next((x for x in companies.values() if x.get("id") == cid), None)
        if not c:
            print(f"{cid} not in company index")
            continue

        ca_count, ca_value = award_rows_for_company(cid)
        ci = get(f"/api/companies/{cid}/competitive-intelligence?peer_limit=5")
        m = next(x for x in ci["benchmark"]["metrics"] if x["key"] == "award_count")
        name = str(c.get("name", ""))[:40]
        print(
            f"{cid:<7} {name:<40} {ca_count:>8} {ca_value:>14,.0f} "
            f"{int(c.get('award_count') or 0):>14} {float(c.get('total_award_value') or 0):>14,.0f} "
            f"{str(m.get('company')):>7} {str(m.get('market_median')):>6} {str(m.get('top_competitor_median')):>7}"
        )

    # Proxy path (Vercel)
    print("\n=== Frontend proxy (tenderscope.ca) ===")
    for cid in [6999, 1921]:
        try:
            url = f"https://tenderscope.ca/api/competitive-intelligence?id={cid}"
            with urllib.request.urlopen(url, timeout=60) as r:
                data = json.loads(r.read())
            m = next(x for x in data["benchmark"]["metrics"] if x["key"] == "award_count")
            print(f"id={cid} proxy CI You={m.get('company')} market={m.get('market_median')}")
        except Exception as exc:
            print(f"id={cid} proxy error: {exc}")


if __name__ == "__main__":
    main()
