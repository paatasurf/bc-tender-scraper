"""Capture baseline opportunities JSON from production for parity tests."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

API = "https://bc-tender-scraper-production.up.railway.app"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "opportunities"

TARGETS = [
    (
        "baseline-construction-1921.json",
        "/api/companies/1921/opportunities?min_score=50&limit=15",
    ),
    (
        "baseline-arch-19.json",
        "/api/arch-companies/19/opportunities?min_score=40&limit=15",
    ),
]


def fetch(path: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(API + path, timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for filename, path in TARGETS:
        out = FIXTURES / filename
        try:
            body = fetch(path)
            data = json.loads(body)
            out.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"Wrote {out} ({len(data.get('matches', []))} matches)")
        except Exception as exc:
            print(f"SKIP {filename}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
