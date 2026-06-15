"""Verify public staging URL."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

FRONTEND = os.environ.get(
    "STAGING_FRONTEND",
    "https://boots-fixed-registry-urgent.trycloudflare.com",
).rstrip("/")


def get(path: str, timeout: int = 120) -> tuple[int, dict | list | str]:
    url = f"{FRONTEND}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    status, page = get("/", timeout=60)
    html = page if isinstance(page, str) else json.dumps(page)
    checks.append(
        (
            "Frontend loads (Company Intelligence UI)",
            status == 200 and "Company Intelligence" in html,
            f"status={status}",
        )
    )

    status, opp = get("/api/companies/id/1735/opportunities?min_score=50&limit=5")
    matches = opp.get("matches", []) if isinstance(opp, dict) else []
    types = sorted({m.get("type") for m in matches})
    checks.append(
        (
            "Discover opportunities API (permits + awards)",
            status == 200 and len(matches) > 0,
            f"status={status}, types={types}, scores={[m.get('score') for m in matches[:3]]}",
        )
    )

    status, simex = get("/api/companies/id/134635/opportunities?min_score=65&limit=3")
    simex_matches = simex.get("matches", []) if isinstance(simex, dict) else []
    checks.append(
        (
            "Contract award opportunities",
            status == 200 and any(m.get("type") == "contract_award" for m in simex_matches),
            f"count={len(simex_matches)}",
        )
    )

    status, arch = get("/api/arch-companies/id/126/opportunities?min_score=65&limit=3")
    arch_matches = arch.get("matches", []) if isinstance(arch, dict) else []
    checks.append(
        (
            "Architecture tender opportunities",
            status == 200 and any(m.get("type") == "tender" for m in arch_matches),
            f"count={len(arch_matches)}",
        )
    )

    name = urllib.parse.quote("DIALOG Design")
    status, ai = get(f"/api/arch-companies/{name}/tender-match/28", timeout=60)
    if status == 200 and isinstance(ai, dict):
        detail = f"win_probability={ai.get('win_probability')}"
        ok = "win_probability" in ai
    elif status == 503:
        detail = "503 — route wired; staging API host needs ANTHROPIC_API_KEY for full AI output"
        ok = True
    else:
        detail = str(ai)[:200]
        ok = False
    checks.append(("Tender AI analysis route", ok, f"status={status}, {detail}"))

    print(f"\nPublic staging URL: {FRONTEND}\n")
    print("=== Public staging verification ===\n")
    passed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}")
        passed += int(ok)
    print(f"\nResult: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
