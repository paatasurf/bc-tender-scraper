"""Deep dive Surrey permit sources: date range, downloads, Open_Data_Download."""
import json
import requests

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"
CURRENT = (
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "IssuedBuildingPermits/FeatureServer/0"
)


def stats(field: str) -> dict:
    r = s.get(
        f"{CURRENT}/query",
        params={
            "where": "1=1",
            "outStatistics": json.dumps(
                [
                    {"statisticType": "min", "onStatisticField": field, "outStatisticFieldName": "min_val"},
                    {"statisticType": "max", "onStatisticField": field, "outStatisticFieldName": "max_val"},
                    {"statisticType": "count", "onStatisticField": field, "outStatisticFieldName": "cnt"},
                ]
            ),
            "f": "json",
        },
        timeout=60,
    )
    return r.json()


print("=== IssuedDate min/max on current layer ===")
print(json.dumps(stats("IssuedDate"), indent=2))

# sample recent vs old
for where in ["1=1", "IssuedDate >= '20250101'", "IssuedDate < '20200101'"]:
    r = s.get(f"{CURRENT}/query", params={"where": where, "returnCountOnly": "true", "f": "json"}, timeout=60)
    print(where, "->", r.json().get("count"))

print("\n=== Hub dataset item f3ec200f59a342e38c52248f07fe610d ===")
r = s.get(
    "https://opendata-surrey.hub.arcgis.com/api/search/v1/collections/dataset/items",
    params={"ids": "f3ec200f59a342e38c52248f07fe610d"},
    timeout=60,
)
print("status", r.status_code)
if r.ok:
    data = r.json()
    for f in data.get("features", []):
        p = f.get("properties", {})
        print("title:", p.get("title"))
        print("description:", (p.get("description") or "")[:400])
        print("modified:", p.get("modified"))
        print("url:", p.get("url"))
        print("links:", json.dumps(p.get("links", [])[:8], indent=2)[:1500])

# Open_Data_Download layer
print("\n=== Open_Data_Download FeatureServer ===")
ODD = (
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "Open_Data_Download/FeatureServer/0"
)
meta = s.get(ODD, params={"f": "pjson"}, timeout=60).json()
print("name:", meta.get("name"))
print("fields:", [f["name"] for f in meta.get("fields", [])])
cnt = s.get(f"{ODD}/query", params={"where": "1=1", "returnCountOnly": "true", "f": "json"}, timeout=60).json()
print("count:", cnt.get("count"))
if cnt.get("count"):
    sample = s.get(
        f"{ODD}/query",
        params={"where": "1=1", "outFields": "*", "resultRecordCount": 5, "f": "json"},
        timeout=60,
    ).json()
    for feat in sample.get("features", []):
        print(" sample:", feat.get("attributes"))

# Hub OGDC download API attempt
print("\n=== ArcGIS Hub download endpoints ===")
item_id = "f3ec200f59a342e38c52248f07fe610d"
for url in [
    f"https://opendata-surrey.hub.arcgis.com/api/download/v1/items/{item_id}/csv",
    f"https://opendata-surrey.hub.arcgis.com/datasets/f3ec200f59a342e38c52248f07fe610d",
    f"https://opendata-surrey.hub.arcgis.com/datasets/surrey::issued-building-permits",
]:
    r = s.get(url, timeout=60, allow_redirects=True)
    print(url)
    print("  status", r.status_code, "final", r.url[:100], "ctype", r.headers.get("content-type", "")[:40], "len", len(r.content))

# gisservices.surrey.ca
print("\n=== gisservices.surrey.ca ===")
try:
    root = s.get("https://gisservices.surrey.ca/arcgis/rest/services", params={"f": "pjson"}, timeout=60).json()
    hits = [x["name"] for x in root.get("services", []) if "permit" in x.get("name", "").lower() or "building" in x.get("name", "").lower()]
    print("services:", hits[:20] or "(none in root)")
except Exception as exc:
    print("error:", exc)

# BC open data
print("\n=== catalogue.data.gov.bc.ca search ===")
try:
    bc = s.get(
        "https://catalogue.data.gov.bc.ca/api/3/action/package_search",
        params={"q": "Surrey building permits", "rows": 10},
        timeout=60,
    ).json()["result"]
    print("count", bc.get("count"))
    for ds in bc.get("results", []):
        print(" ", ds.get("title"), "|", ds.get("name"))
        for res in ds.get("resources", [])[:3]:
            print("   ", res.get("format"), res.get("url", "")[:100])
except Exception as exc:
    print("error:", exc)
