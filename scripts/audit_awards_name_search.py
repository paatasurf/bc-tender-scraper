"""Search production for award winners matching known GC names."""
from __future__ import annotations

import json
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"
NAMES = [
    "LMDG",
    "Fusion Projects",
    "Heatherbrae",
    "Reotech",
    "GHL Consultants",
    "Sasco",
    "Ventana Construction",
    "Matra Construction",
]


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def main():
    # fetch all awards (4360 rows)
    rows = []
    offset = 0
    while True:
        page = get(f"/api/contract-awards?limit=500&offset={offset}")
        batch = page["data"]
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    print(f"Total awards fetched: {len(rows)}")

    for needle in NAMES:
        hits = [
            r
            for r in rows
            if needle.lower() in (r.get("winner_company") or "").lower()
            or needle.lower() in (r.get("title") or "").lower()
        ]
        print(f"\n{needle}: {len(hits)} hits")
        for h in hits[:3]:
            print(
                f"  company_id={h.get('company_id')} winner={h.get('winner_company','')[:60]} "
                f"source={h.get('source')} value={h.get('award_value')}"
            )

    # companies search API
    print("\n=== Company name search ===")
    for needle in NAMES:
        page = get(f"/api/companies?search={urllib.parse.quote(needle)}&limit=5")
        for c in page.get("data", []):
            print(
                f"  search={needle} id={c['id']} awards={c.get('award_count')} "
                f"projects={c.get('total_projects')} name={c.get('name','')[:55]}"
            )


if __name__ == "__main__":
    import urllib.parse

    main()
