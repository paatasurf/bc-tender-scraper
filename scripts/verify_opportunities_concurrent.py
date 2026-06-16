"""Concurrent opportunities discovery load test (pool exhaustion guard)."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_BASE = "http://127.0.0.1:8000"
DISCOVER_PATHS = [
    "/api/companies/1921/opportunities?min_score=50&limit=15",
    "/api/companies/1735/opportunities?min_score=50&limit=15",
    "/api/arch-companies/19/opportunities?min_score=40&limit=15",
    "/api/companies/1921/opportunities?min_score=50&limit=15",
    "/api/arch-companies/19/opportunities?min_score=40&limit=15",
]
PROBE_PATHS = [
    "/api/health",
    "/api/permits?limit=10",
]


def fetch(base: str, path: str, timeout: float = 120.0) -> tuple[str, int | str, float]:
    url = base + path
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
            return path, resp.status, time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        return path, exc.code, time.perf_counter() - started
    except Exception as exc:
        return path, f"ERR:{exc}", time.perf_counter() - started


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    print(f"Base URL: {base}")
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, base, path) for path in DISCOVER_PATHS]
        probe_futures = [pool.submit(fetch, base, path, 10.0) for path in PROBE_PATHS]
        for future in as_completed(futures + probe_futures):
            path, code, elapsed = future.result()
            print(f"{path} -> {code} ({elapsed:.1f}s)")
            if code != 200:
                failures.append(f"{path}: {code}")

    if failures:
        print("FAILURES:")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("All concurrent requests succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
