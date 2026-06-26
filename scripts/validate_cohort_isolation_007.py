"""Validate Feature 007 GC allowlist on Pontem Group and Mo Maani."""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

GC_ALLOWLIST = re.compile(
    r"construction|contracting|contractor|builder|builders|building|development|"
    r"developments|homes|renovations|restoration|remodeling",
    re.I,
)

NON_GC_PATTERN = re.compile(
    r"architect|architecture|engineer|engineering|consult|design studio|architrix|"
    r"designs group|interior design|landscape|surveyor|inspection|office environments|"
    r"office interiors|fit-out|space planning",
    re.I,
)


GC_COMPANY_TYPES = {"general contractor", "trade contractor"}


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def find_company_id(name_fragment: str) -> int | None:
    page = get(f"/api/companies?search={urllib.parse.quote(name_fragment)}&limit=10")
    for row in page.get("data", []):
        if name_fragment.lower() in (row.get("name") or "").lower():
            return int(row["id"])
    return None


def peer_classification_text(peer: dict, company_row: dict | None) -> str:
    parts = [peer.get("name", "")]
    if company_row:
        parts.extend(
            [
                company_row.get("company_type", ""),
                company_row.get("primary_trade", ""),
                company_row.get("dominant_sector", ""),
            ]
        )
        parts.extend(company_row.get("project_types") or [])
    return " ".join(str(p) for p in parts if p)


def is_gc_type_competitor(peer: dict, company_row: dict | None) -> bool:
    text = peer_classification_text(peer, company_row)
    if NON_GC_PATTERN.search(text):
        return False
    company_type = ((company_row or {}).get("company_type") or "").strip().lower()
    if company_type in GC_COMPANY_TYPES:
        return True
    return bool(GC_ALLOWLIST.search(text))


def validate_profile(company_id: int, label: str, company_index: dict[int, dict]) -> bool:
    ci = get(f"/api/companies/{company_id}/competitive-intelligence?peer_limit=5")
    peers = ci.get("top_competitors", [])
    violations: list[str] = []
    print(f"\n{label} (id={company_id}) engine={ci.get('engine_version')} cohort={ci.get('market', {}).get('cohort_size')}")
    for peer in peers:
        row = company_index.get(int(peer["company_id"]))
        text = peer_classification_text(peer, row)
        name = peer.get("name", "")
        if NON_GC_PATTERN.search(text):
            violations.append(name)
        print(f"  - {name} (threat={peer.get('threat_score')})")
    if violations:
        print(f"  VIOLATIONS (non-GC patterns): {violations}")
        return False
    print("  OK — no consultants, architects, or design firms in top competitors")
    return True


def main() -> None:
    company_index: dict[int, dict] = {}
    offset = 0
    while True:
        page = get(f"/api/companies?limit=500&offset={offset}")
        for row in page["data"]:
            company_index[int(row["id"])] = row
        if len(page["data"]) < 500:
            break
        offset += 500

    targets = [
        ("Pontem Group", "Jack Hui DBA: Pontem Group"),
        ("Mo Maani", "Mo Maani"),
    ]
    ok = True
    for fragment, label in targets:
        cid = find_company_id(fragment)
        if cid is None:
            print(f"SKIP {label}: company not found")
            ok = False
            continue
        if not validate_profile(cid, label, company_index):
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
