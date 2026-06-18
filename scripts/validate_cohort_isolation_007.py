"""Validate Feature 007 cohort isolation on Mo Maani and 2 other GC profiles."""
from __future__ import annotations

import json
import re
import sys
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

EXCLUSION_PATTERN = re.compile(
    r"code consultant|building code|building envelope|designs group|interior design|"
    r"landscape|architect|architecture|engineer|engineering|surveyor|inspection|consulting",
    re.I,
)


def get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=120) as r:
        return json.loads(r.read())


def find_company_id(name_fragment: str) -> int | None:
    page = get(f"/api/companies?search={urllib.parse.quote(name_fragment)}&limit=10")
    for row in page.get("data", []):
        if name_fragment.lower() in (row.get("name") or "").lower():
            return int(row["id"])
    return None


def validate_profile(company_id: int, label: str) -> bool:
    ci = get(f"/api/companies/{company_id}/competitive-intelligence?peer_limit=5")
    peers = ci.get("top_competitors", [])
    violations = []
    for peer in peers:
        name = peer.get("name", "")
        if EXCLUSION_PATTERN.search(name):
            violations.append(name)
    print(f"\n{label} (id={company_id}) engine={ci.get('engine_version')} cohort={ci.get('market', {}).get('cohort_size')}")
    if not peers:
        print("  no top competitors")
    for peer in peers:
        print(f"  - {peer.get('name')} (threat={peer.get('threat_score')})")
    if violations:
        print(f"  VIOLATIONS: {violations}")
        return False
    print("  OK — no code consultants or design firms in top competitors")
    return True


def main() -> None:
    import urllib.parse

    targets = [
        ("Mo Maani", "Mo Maani"),
        ("Fusion Projects", "Fusion Projects GC"),
        ("Heatherbrae", "Heatherbrae Builders"),
    ]
    ok = True
    for fragment, label in targets:
        cid = find_company_id(fragment)
        if cid is None:
            print(f"SKIP {label}: company not found")
            ok = False
            continue
        if not validate_profile(cid, label):
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
