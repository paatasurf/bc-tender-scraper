"""Validate Feature 006 competitive intelligence in production."""
import json
import time
import urllib.error
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"

CASES = [
    ("construction", 1921, "Large GC (commercial)"),
    ("construction", 1735, "Known test company"),
    ("construction", 134635, "Award-heavy company"),
    ("construction", 670, "Fusion Projects peer"),
    ("construction", 42, "Smaller profile"),
    ("architecture", 126, "Arch firm"),
    ("architecture", 19, "Arch firm 2"),
]


def get(path: str, timeout: int = 180) -> tuple[int, object, float]:
    t0 = time.perf_counter()
    url = API + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read())
            return r.status, body, round(time.perf_counter() - t0, 2)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return e.code, body, round(time.perf_counter() - t0, 2)


def validate(data: dict) -> list[str]:
    issues: list[str] = []
    if data.get("engine_version") != "competitive_intel_v1":
        issues.append("bad engine_version")
    metrics = data.get("benchmark", {}).get("metrics", [])
    if len(metrics) != 5:
        issues.append(f"expected 5 metrics got {len(metrics)}")
    keys = {m["key"] for m in metrics}
    expected = {
        "total_projects",
        "total_value",
        "avg_project_value",
        "award_count",
        "ai_reliability_score",
    }
    if keys != expected:
        issues.append(f"metric keys mismatch {keys}")
    if data.get("kind") == "architecture":
        aw = next((m for m in metrics if m["key"] == "award_count"), None)
        if aw and not aw.get("not_applicable"):
            issues.append("arch award_count should be N/A")
    peers = data.get("top_competitors", [])
    warnings = data.get("warnings", [])
    if "insufficient_market_data" in warnings:
        if peers:
            issues.append("warnings say insufficient but peers returned")
    else:
        if len(peers) < 3:
            issues.append(f"expected >=3 peers got {len(peers)}")
        if len(peers) > 5:
            issues.append(f"expected <=5 peers got {len(peers)}")
    for p in peers:
        tb = p.get("threat_breakdown", {})
        score = tb.get("score", p.get("threat_score"))
        pts = sum(c.get("points", 0) for c in tb.get("breakdown", []))
        if score != pts:
            issues.append(f"peer {p.get('name')}: score {score} != sum {pts}")
        for c in tb.get("breakdown", []):
            d = c.get("detail", "")
            if any(x in d.lower() for x in [" street", " avenue", " ave ", " st,"]):
                issues.append(f"street token in geo detail: {d[:80]}")
    return issues


def main() -> None:
    print("=== Feature 006 Production Validation ===")
    print("API:", API)
    print()

    for path in [
        "/api/companies/1921/competitive-intelligence",
        "/api/companies/id/1921/competitive-intelligence",
    ]:
        code, body, elapsed = get(path + "?peer_limit=5")
        print(f"Route {path}: {code} in {elapsed}s")
        if code != 200:
            raise SystemExit(1)

    results = []
    for kind, cid, label in CASES:
        base = "arch-companies" if kind == "architecture" else "companies"
        code, body, elapsed = get(
            f"/api/{base}/{cid}/competitive-intelligence?peer_limit=5"
        )
        if code != 200:
            results.append((label, cid, "FAIL", f"HTTP {code}: {body}", elapsed))
            continue
        issues = validate(body)
        peers = len(body.get("top_competitors", []))
        cohort = body["market"].get("cohort_size", 0)
        top_threat = (
            body["top_competitors"][0]["threat_score"] if peers else None
        )
        status = "PASS" if not issues else "WARN"
        summary = (
            f"cohort={cohort} peers={peers} top_threat={top_threat} elapsed={elapsed}s"
        )
        if issues:
            summary += " | " + "; ".join(issues)
        results.append((label, cid, status, summary, elapsed))

    print()
    for label, cid, status, summary, _elapsed in results:
        print(f"[{status}] {label} (id={cid})")
        print(f"       {summary}")
        print()

    slow = [r for r in results if r[4] > 2.0 and r[2] != "FAIL"]
    if slow:
        print("SLOW (>2s):", [(r[0], r[4]) for r in slow])

    # Determinism check on 1921
    _, b1, _ = get("/api/companies/1921/competitive-intelligence?peer_limit=5")
    _, b2, _ = get("/api/companies/1921/competitive-intelligence?peer_limit=5")
    peer_ids_1 = [p["company_id"] for p in b1.get("top_competitors", [])]
    peer_ids_2 = [p["company_id"] for p in b2.get("top_competitors", [])]
    print("Determinism 1921:", "OK" if peer_ids_1 == peer_ids_2 else f"MISMATCH {peer_ids_1} vs {peer_ids_2}")


def profile_report() -> None:
    profiles = [
        ("construction", 1921, "David Steer / LMDG"),
        ("construction", 1735, "GHL Consultants"),
        ("construction", 670, "Fusion Projects"),
        ("construction", 42, "Small GC"),
        ("architecture", 126, "Arch firm"),
    ]
    for kind, cid, name in profiles:
        base = "arch-companies" if kind == "architecture" else "companies"
        _, d, elapsed = get(
            f"/api/{base}/{cid}/competitive-intelligence?peer_limit=5"
        )
        bm = d["benchmark"]["metrics"]
        print(f"## {name} ({kind} id={cid}) [{elapsed}s]")
        print("Market:", d["market"]["definition"])
        print("Warnings:", d.get("warnings") or "none")
        print("Benchmark (You | Market | Top-Rival):")
        for m in bm:
            na = " N/A" if m.get("not_applicable") else ""
            print(
                f"  {m['label']}: {m['company']} | "
                f"{m['market_median']} | {m['top_competitor_median']}{na}"
            )
        print("Top Competitors:")
        for p in d["top_competitors"]:
            tb = p["threat_breakdown"]
            parts = ", ".join(
                f"{c['factor']}={c['points']}" for c in tb["breakdown"]
            )
            print(f"  {p['threat_score']} {p['name'][:45]} ({parts})")
        print()


if __name__ == "__main__":
    main()
    print("=== Profile Detail ===")
    profile_report()
