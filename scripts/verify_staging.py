"""Verify staging deployment for opportunity discovery."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("STAGING_API", "https://proud-eggs-sit.loca.lt").rstrip("/")
FRONTEND = os.environ.get("STAGING_FRONTEND", "https://empty-coats-grin.loca.lt").rstrip("/")
TUNNEL_HEADERS = {"Bypass-Tunnel-Reminder": "true", "Accept": "application/json"}
TEST_COMPANY_ID = 1735
TEST_COMPANY_SEARCH = "GHL Consultants"


def fetch(url: str, timeout: int = 60) -> tuple[int, dict | list | str]:
    req = urllib.request.Request(url, headers={**TUNNEL_HEADERS, "User-Agent": "staging-verify/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        try:
            return resp.status, json.loads(body)
        except json.JSONDecodeError:
            return resp.status, body


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    try:
        status, health = fetch(f"{API}/api/health")
        checks.append(("Staging API health", status == 200, str(health)))
    except Exception as exc:
        checks.append(("Staging API health", False, str(exc)))

    try:
        status, opp = fetch(f"{API}/api/companies/{TEST_COMPANY_ID}/opportunities?min_score=50&limit=5")
        matches = opp.get("matches", []) if isinstance(opp, dict) else []
        types = sorted({m.get("type") for m in matches})
        checks.append(
            (
                "Opportunities endpoint returns data",
                status == 200 and len(matches) > 0,
                f"status={status}, count={len(matches)}, types={types}",
            )
        )
    except Exception as exc:
        checks.append(("Opportunities endpoint returns data", False, str(exc)))

    try:
        status, proxy = fetch(
            f"{FRONTEND}/api/companies/id/{TEST_COMPANY_ID}/opportunities?min_score=50&limit=5"
        )
        proxy_matches = proxy.get("matches", []) if isinstance(proxy, dict) else []
        checks.append(
            (
                "Frontend opportunities proxy",
                status == 200 and len(proxy_matches) > 0,
                f"status={status}, count={len(proxy_matches)}",
            )
        )
    except Exception as exc:
        checks.append(("Frontend opportunities proxy", False, str(exc)))

    try:
        status, page = fetch(FRONTEND, timeout=30)
        html = page if isinstance(page, str) else json.dumps(page)
        checks.append(
            (
                "Discover Opportunities UI text present",
                status == 200 and "Discover opportunities" in html,
                f"status={status}",
            )
        )
    except Exception as exc:
        checks.append(("Discover Opportunities UI text present", False, str(exc)))

    # Tender AI route (503 acceptable without API key; 502/404 fail)
    try:
        name = urllib.parse.quote("Andrew Harmsworth DBA: GHL Consultants Ltd")
        tender_id = next(
            m["payload"]["id"]
            for m in matches
            if m.get("type") == "tender"
        ) if "matches" in dir() and matches else 36
        status, body = fetch(f"{API}/api/companies/{name}/tender-match/{tender_id}", timeout=30)
        ok = status in (200, 503)
        detail = f"status={status}"
        if status == 200 and isinstance(body, dict):
            detail += f", win_probability={body.get('win_probability')}"
        elif status == 503:
            detail += " (route wired; set ANTHROPIC_API_KEY on staging API for full AI response)"
        checks.append(("Tender AI analysis endpoint", ok, detail))
    except Exception as exc:
        checks.append(("Tender AI analysis endpoint", False, str(exc)))

    print(f"\nStaging API:      {API}")
    print(f"Staging Frontend: {FRONTEND}\n")
    print("=== Staging verification ===\n")
    passed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}\n       {detail}")
        passed += int(ok)
    print(f"\nResult: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
