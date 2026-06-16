"""Verify construction opportunities endpoint after deploy."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"
PATH = "/api/companies/1921/opportunities?min_score=50&limit=15"


def fetch(path: str, timeout: int = 120) -> tuple[int | str, float, int, bytes]:
    url = API + path
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, time.perf_counter() - started, len(body), body
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, time.perf_counter() - started, len(body), body
    except Exception as exc:
        return "ERR", time.perf_counter() - started, 0, str(exc).encode()


def main() -> int:
    print(f"API: {API}")
    code, elapsed, _, _ = fetch("/api/health", timeout=30)
    print(f"health: {code} ({elapsed:.1f}s)")
    if code != 200:
        return 1

    print(f"GET {PATH}")
    code, elapsed, size, body = fetch(PATH, timeout=120)
    print(f"status={code} response_time={elapsed:.1f}s bytes={size}")
    if code != 200:
        preview = body[:300].decode("utf-8", errors="replace")
        print(f"body preview: {preview}")
        return 1

    data = json.loads(body)
    matches = data.get("matches", [])
    tender_with_breakdown = sum(
        1 for m in matches if m.get("type") == "tender" and m.get("breakdown")
    )
    print(f"total_candidates={data.get('total_candidates')}")
    print(f"final_matches={len(matches)}")
    print(f"tender_breakdowns={tender_with_breakdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
