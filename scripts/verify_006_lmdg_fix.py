"""Verify LMDG (1921) competitive intel fixes in production."""
import json
import time
import urllib.request

API = "https://bc-tender-scraper-production.up.railway.app"
FRONT = "https://tenderscope.ca"


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read())


def main() -> None:
    for i in range(20):
        try:
            data = get(f"{API}/api/companies/1921/competitive-intelligence?peer_limit=5")
            peers = [
                (p["company_id"], p["name"][:40], p["threat_score"])
                for p in data["top_competitors"]
            ]
            simex = any("Simex" in name for _, name, _ in peers)
            geo = [
                c["points"]
                for p in data["top_competitors"]
                for c in p["threat_breakdown"]["breakdown"]
                if c["factor"] == "geographic_overlap"
            ]
            print(f"attempt {i + 1}: peers={peers}")
            print(f"  simex_present={simex} geo_points={geo}")
            if not simex and geo and min(geo) >= 10:
                print("BACKEND OK")
                break
        except Exception as exc:
            print(f"attempt {i + 1}: {exc}")
        time.sleep(15)

    try:
        front = get(f"{FRONT}/api/competitive-intelligence?id=1921&peer_limit=5")
        print("FRONT peers:", [p["name"][:35] for p in front["top_competitors"]])
    except Exception as exc:
        print("FRONT proxy:", exc)


if __name__ == "__main__":
    main()
