"""Inspect issued building permits catalog entries and hub content API."""
import json
import requests

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"
ODD = (
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "Open_Data_Download/FeatureServer/0"
)

queries = [
    "Dataset_Name = 'issued building permits'",
    "Dataset_Name = 'issued building permits '",
    "Dataset_Name = 'building division permits issued'",
    "Dataset_Name LIKE 'issued building%'",
]
for where in queries:
    r = s.get(
        f"{ODD}/query",
        params={
            "where": where,
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=60,
    ).json()
    print(f"\n{where}: {len(r.get('features', []))} rows")
    for feat in r.get("features", []):
        print(" ", feat.get("attributes"))

# Hub content API (ArcGIS Online item)
item = "f3ec200f59a342e38c52248f07fe610d"
for url in [
    f"https://www.arcgis.com/sharing/rest/content/items/{item}?f=pjson",
    f"https://www.arcgis.com/sharing/rest/content/items/{item}/data?f=pjson",
]:
    r = s.get(url, timeout=60)
    print(f"\n{url} status={r.status_code}")
    if r.ok:
        data = r.json()
        for key in ["title", "type", "typeKeywords", "url", "description", "modified", "size"]:
            if key in data:
                val = data[key]
                if key == "description":
                    val = str(val)[:300]
                print(f"  {key}: {val}")

# Search AGOL for surrey issued building permits
r = s.get(
    "https://www.arcgis.com/sharing/rest/search",
    params={
        "q": 'title:"Issued Building Permits" AND owner:surrey*',
        "f": "pjson",
        "num": 10,
    },
    timeout=60,
).json()
print("\n=== AGOL search ===")
for res in r.get("results", []):
    print(res.get("title"), "|", res.get("type"), "|", res.get("url", "")[:100])

# broader search
r2 = s.get(
    "https://www.arcgis.com/sharing/rest/search",
    params={"q": "Issued Building Permits Surrey", "f": "pjson", "num": 20},
    timeout=60,
).json()
print("\n=== AGOL broad search ===")
for res in r2.get("results", []):
    print(res.get("title"), "|", res.get("id"), "|", (res.get("url") or "")[:90])

# List formats for building permit summary archives in Open_Data_Download
r = s.get(
    f"{ODD}/query",
    params={
        "where": "Dataset_Name LIKE '%building permit summary%'",
        "outFields": "Dataset_Name,Format",
        "returnDistinctValues": "true",
        "returnGeometry": "false",
        "f": "json",
    },
    timeout=60,
).json()
formats = {}
for feat in r.get("features", []):
    a = feat.get("attributes") or {}
    formats.setdefault(a.get("Dataset_Name"), set()).add(a.get("Format"))
print("\n=== building permit summary formats (sample) ===")
for name in sorted(formats)[:15]:
    print(name, "->", sorted(formats[name]))
