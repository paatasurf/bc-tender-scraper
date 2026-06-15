"""Local staging verification before public deploy."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

API = os.environ.get("STAGING_API", "http://127.0.0.1:8000").rstrip("/")
FRONTEND = os.environ.get("STAGING_FRONTEND", "http://127.0.0.1:3020").rstrip("/")

COMPANIES = [
    (1735, "Andrew Harmsworth DBA: GHL Consultants Ltd", "construction", 50),
    (134635, "Simex Defence Inc.", "construction", 65),
    (126, "DIALOG Design", "architecture", 65),
]


def fetch(url: str, timeout: int = 120) -> tuple[int, dict | list | str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    try:
        status, health = fetch(f"{API}/api/health", timeout=10)
        checks.append(("API health", status == 200, str(health)))
    except Exception as exc:
        checks.append(("API health", False, str(exc)))
        health = {}

    try:
        status, _ = fetch(FRONTEND, timeout=30)
        checks.append(("Frontend reachable", status == 200, f"status={status}"))
    except Exception as exc:
        checks.append(("Frontend reachable", False, str(exc)))

    all_types: set[str] = set()
    tender_probe: tuple[str, int] | None = None
    for company_id, name, kind, min_score in COMPANIES:
        path = (
            f"{API}/api/arch-companies/{company_id}/opportunities?min_score={min_score}&limit=10"
            if kind == "architecture"
            else f"{API}/api/companies/{company_id}/opportunities?min_score={min_score}&limit=10&kind=construction"
        )
        try:
            status, data = fetch(path)
            matches = data.get("matches", []) if isinstance(data, dict) else []
            types = {m.get("type") for m in matches}
            all_types |= types
            scores = [m.get("score") for m in matches[:3]]
            rules = all(m.get("source") == "rules" for m in matches)
            checks.append(
                (
                    f"Opportunities for {name[:30]}",
                    status == 200 and len(matches) > 0 and rules,
                    f"status={status}, types={sorted(types)}, scores={scores}",
                )
            )
            if tender_probe is None:
                for m in matches:
                    if m.get("type") == "tender":
                        tender_probe = (name, m["payload"]["id"])
                        break
        except Exception as exc:
            checks.append((f"Opportunities for {name[:30]}", False, str(exc)))

    checks.append(
        (
            "Internal match scores cover tender/permit/contract_award",
            {"tender", "permit", "contract_award"}.issubset(all_types),
            f"seen={sorted(all_types)}",
        )
    )

    try:
        status, proxy = fetch(
            f"{FRONTEND}/api/companies/id/1735/opportunities?min_score=50&limit=5",
            timeout=120,
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

    if tender_probe:
        name, tender_id = tender_probe
        enc = urllib.parse.quote(name)
        base = "arch-companies" if "DIALOG" in name else "companies"
        try:
            status, body = fetch(f"{API}/api/{base}/{enc}/tender-match/{tender_id}", timeout=60)
            if status == 200:
                detail = f"status=200, win_probability={body.get('win_probability')}"
                ok = "win_probability" in body
            elif status == 503:
                detail = "status=503 route wired (ANTHROPIC_API_KEY missing locally)"
                ok = True
            else:
                detail = f"status={status}, body={str(body)[:160]}"
                ok = False
            checks.append(("Tender AI analysis endpoint", ok, detail))
        except Exception as exc:
            checks.append(("Tender AI analysis endpoint", False, str(exc)))
    else:
        checks.append(("Tender AI analysis endpoint", False, "No tender match sample found"))

    print(f"\nLocal API:      {API}")
    print(f"Local Frontend: {FRONTEND}\n")
    print("=== Local staging verification ===\n")
    passed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}")
        passed += int(ok)
    print(f"\nResult: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
