"""Before/after tender visibility report via production API."""
from __future__ import annotations

import json
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

COMPANY_IDS = [1735, 1517, 8756, 292, 5, 102, 84, 213, 268, 1921, 670, 420]

BEFORE = {
    1735: {"tender": 0, "permit": 2},
    1517: {"tender": 0, "permit": 1},
    8756: {"tender": 0, "permit": 1},
    292: {"tender": 0, "permit": 15},
    5: {"tender": 0, "permit": 15},
    102: {"tender": 0, "permit": 15},
    84: {"tender": 0, "permit": 15},
    213: {"tender": 0, "permit": 15},
    268: {"tender": 0, "permit": 15},
    1921: {"tender": 0, "permit": 10},
    670: {"tender": 0, "permit": 11},
    420: {"tender": 0, "permit": 3},
}


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    companies = {c["id"]: c["name"] for c in fetch(f"{API}/api/companies?limit=500")["data"]}
    print("Construction Intelligence — tender visibility before/after")
    print(f"Production: {API}")
    print()
    print(f"{'ID':>6}  {'Company':<36}  {'T before':>8}  {'T after':>7}  {'P before':>8}  {'P after':>7}  {'Total':>5}")
    print("-" * 92)

    sum_tb = sum_ta = 0
    for cid in COMPANY_IDS:
        opp = fetch(
            f"{API}/api/companies/{cid}/opportunities?min_score=65&limit=15&kind=construction"
        )
        after = {"tender": 0, "permit": 0, "contract_award": 0}
        for m in opp.get("matches", []):
            after[m["type"]] = after.get(m["type"], 0) + 1
        before = BEFORE[cid]
        name = companies.get(cid, "?")[:36]
        total = len(opp.get("matches", []))
        print(
            f"{cid:6}  {name:<36}  {before['tender']:8}  {after.get('tender', 0):7}  "
            f"{before['permit']:8}  {after.get('permit', 0):7}  {total:5}"
        )
        sum_tb += before["tender"]
        sum_ta += after.get("tender", 0)

    print("-" * 92)
    print(f"{'TOTAL':>6}  {'(12 companies)':<36}  {sum_tb:8}  {sum_ta:7}")
    print()
    print("ranking_model:", opp.get("ranking_model", "n/a"))


if __name__ == "__main__":
    main()
