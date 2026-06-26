"""Probe production endpoints to see which work without Claude calls."""
import json
import urllib.error
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"


def probe(path: str, method: str = "GET", body: bytes | None = None) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(API + path, method=method, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            if r.headers.get("content-type", "").startswith("application/json"):
                return r.status, json.loads(raw)
            return r.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw[:300]


def brief(data: object) -> str:
    if not isinstance(data, dict):
        return str(data)[:120]
    if "detail" in data:
        return str(data["detail"])[:120]
    if "anthropic_api_key_configured" in data:
        return f"key_configured={data['anthropic_api_key_configured']}"
    if "engine_version" in data:
        return data["engine_version"]
    if "status" in data and "matches" in data:
        return f"matches={len(data['matches'])}"
    if "data" in data:
        return f"rows={len(data['data'])}"
    if "sections" in data:
        return f"sections={len(data['sections'])}"
    return str(list(data.keys())[:6])


def main() -> None:
    checks: list[tuple[str, str, str, bytes | None]] = [
        ("health", "GET", "/api/health", None),
        ("companies list", "GET", "/api/companies?limit=2", None),
        ("opportunities", "GET", "/api/companies/1921/opportunities?limit=5", None),
        ("bd-intelligence", "GET", "/api/companies/1921/bd-intelligence", None),
        ("capability-profile", "GET", "/api/companies/1921/capability-profile", None),
        ("competitive-intel", "GET", "/api/companies/1921/competitive-intelligence?peer_limit=5", None),
        ("ai-matching construction sync", "POST", "/api/ai-matching", json.dumps({"sync": True, "kind": "construction", "company_id": 1921, "limit": 3}).encode()),
        ("ai-matching architecture sync", "POST", "/api/ai-matching", json.dumps({"sync": True, "kind": "architecture", "company_id": 126, "limit": 3}).encode()),
        ("chat", "POST", "/api/chat", json.dumps({"message": "hello"}).encode()),
    ]
    print("Production probe:", API)
    for name, method, path, body in checks:
        code, data = probe(path, method, body)
        print(f"  {name}: HTTP {code} — {brief(data)}")


if __name__ == "__main__":
    main()
