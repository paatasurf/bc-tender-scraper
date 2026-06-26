"""Probe Vercel frontend deployment for Feature 006."""
import json
import urllib.error
import urllib.request

FRONT = "https://construction-dashboard.vercel.app"


def get(path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(FRONT + path, timeout=60) as r:
            ct = r.headers.get("content-type", "")
            body = r.read()
            if "json" in ct:
                return r.status, json.loads(body)
            return r.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw[:500]


def main() -> None:
    checks = [
        "/",
        "/api/companies/id/1921/competitive-intelligence?peer_limit=5",
        "/api/companies/id/1921/bd-intelligence?kind=construction",
    ]
    print("Frontend:", FRONT)
    for path in checks:
        code, data = get(path)
        if isinstance(data, dict):
            if data.get("engine_version"):
                brief = (
                    f"{data['engine_version']} peers="
                    f"{len(data.get('top_competitors', []))}"
                )
            elif "error" in data:
                brief = str(data["error"])[:150]
            else:
                brief = str(list(data.keys())[:8])
        else:
            brief = f"html_len={len(data)}"
            for term in (
                "Competitive Intelligence",
                "competitive-intelligence",
                "Benchmark",
                "Threat Score",
            ):
                if term in data:
                    brief += f" HAS_{term.replace(' ', '_')}"
        print(f"  {path} -> HTTP {code}: {brief}")


if __name__ == "__main__":
    main()
