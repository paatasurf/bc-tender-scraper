"""Probe ArcGIS Hub and alternate Surrey permit endpoints."""
import json
import requests

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"


def count_layer(base: str) -> dict:
    meta = s.get(base, params={"f": "pjson"}, timeout=60).json()
    cnt = s.get(
        f"{base}/query",
        params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
        timeout=60,
    ).json()
    return {
        "name": meta.get("name"),
        "description": (meta.get("description") or "")[:250],
        "fields": [f["name"] for f in meta.get("fields", [])],
        "count": cnt.get("count"),
        "error": cnt.get("error"),
    }


layers = [
    (
        "current scraper",
        "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
        "IssuedBuildingPermits/FeatureServer/0",
    ),
    (
        "web search alt",
        "https://services.arcgis.com/84l75S7pP429sV89/arcgis/rest/services/"
        "Issued_Building_Permits/FeatureServer/0",
    ),
    (
        "web search alt v2",
        "https://services.arcgis.com/84l75S7pP429sV89/arcgis/rest/services/"
        "IssuedBuildingPermits/FeatureServer/0",
    ),
]

for label, url in layers:
    print(f"\n=== {label} ===")
    print(url)
    try:
        info = count_layer(url)
        print(json.dumps(info, indent=2))
    except Exception as exc:
        print("error:", exc)

# ArcGIS Hub search API
print("\n=== ArcGIS Hub dataset search ===")
for q in ["building permits", "issued building"]:
    r = s.get(
        "https://opendata-surrey.hub.arcgis.com/api/search/v1/collections/dataset/items",
        params={"q": q, "limit": 10},
        timeout=60,
    )
    print("status", r.status_code, "q=", q)
    if r.ok:
        data = r.json()
        print("matched", data.get("numberMatched"))
        for f in data.get("features", []):
            p = f.get("properties", {})
            print(" ", p.get("title"), "|", p.get("id"))
            print("   url:", p.get("url") or p.get("landingPage"))

# Try hub dataset detail for building-permits id
print("\n=== Hub item building-permits ===")
for path in [
    "https://opendata-surrey.hub.arcgis.com/api/search/v1/collections/dataset/items/building-permits",
    "https://opendata-surrey.hub.arcgis.com/api/search/v1/collections/dataset/items?filter=id:building-permits",
]:
    r = s.get(path, timeout=60)
    print(path, r.status_code)
    if r.ok:
        data = r.json()
        if "features" in data:
            for f in data["features"][:3]:
                print(json.dumps(f.get("properties", {}), indent=2)[:1200])
        else:
            print(json.dumps(data, indent=2)[:1200])

# services5 all services
print("\n=== services5 all services ===")
root = s.get(
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services",
    params={"f": "pjson"},
    timeout=60,
).json()
for svc in root.get("services", []):
    print(svc)
