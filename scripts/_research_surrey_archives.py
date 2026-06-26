"""Search Surrey Open_Data_Download and archives for building permits."""
import json
import re
import requests

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"
ODD = (
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "Open_Data_Download/FeatureServer/0"
)

for term in ["building", "permit", "issued"]:
    where = f"Dataset_Name LIKE '%{term}%'"
    r = s.get(
        f"{ODD}/query",
        params={
            "where": where,
            "outFields": "Dataset_Name,Year,Month,Format,Category",
            "returnDistinctValues": "true",
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=60,
    )
    payload = r.json()
    print(f"\n=== Open_Data_Download where {where} ===")
    if payload.get("error"):
        print(payload["error"])
        continue
    names = sorted({(f.get("attributes") or {}).get("Dataset_Name") for f in payload.get("features", [])})
    print("distinct datasets:", len(names))
    for name in names[:30]:
        print(" ", name)

# count building permit related downloads
for where in [
    "Dataset_Name LIKE '%Building Permit%'",
    "Dataset_Name LIKE '%building permit%'",
    "Dataset_Name LIKE '%Issued Building%'",
]:
    r = s.get(f"{ODD}/query", params={"where": where, "returnCountOnly": "true", "f": "json"}, timeout=60)
    print(where, "rows:", r.json().get("count"))

# scrape hub dataset page for service + export hints
print("\n=== Hub page embedded JSON hints ===")
html = s.get("https://opendata-surrey.hub.arcgis.com/datasets/surrey::issued-building-permits", timeout=60).text
for pat in [
    r"https://services[^\"']+",
    r"recordCount[^,]{0,80}",
    r"totalCount[^,]{0,80}",
    r"download[^\"']{0,120}",
    r"csv[^\"']{0,120}",
    r"8,?330",
    r"290",
]:
    hits = re.findall(pat, html, re.I)
    if hits:
        print(pat, "->", hits[:5])

# search all services5 for archive + building
root = s.get(
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services",
    params={"f": "pjson"},
    timeout=60,
).json()
archive_hits = [
    svc["name"]
    for svc in root.get("services", [])
    if any(x in svc.get("name", "").lower() for x in ("archive", "building", "permit", "issued"))
]
print("\n=== services5 archive/building/permit names ===")
for name in archive_hits:
    print(" ", name)

# probe timages archive service if exists
for name in archive_hits:
    if "building" not in name.lower() and "permit" not in name.lower():
        continue
    base = f"https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/{name}/FeatureServer/0"
    try:
        meta = s.get(base, params={"f": "pjson"}, timeout=60).json()
        cnt = s.get(f"{base}/query", params={"where": "1=1", "returnCountOnly": "true", "f": "json"}, timeout=60).json()
        print(f"\n{name}: count={cnt.get('count')} fields={[f['name'] for f in meta.get('fields', [])][:10]}")
    except Exception as exc:
        print(name, exc)

# development statistics page links
print("\n=== Development statistics PDF links ===")
html2 = s.get(
    "https://www.surrey.ca/renovating-building-development/land-planning-development/development-statistics",
    timeout=60,
).text
pdfs = sorted(set(re.findall(r'href="([^"]+\.pdf[^"]*)"', html2, re.I)))
print("pdf count", len(pdfs))
for u in pdfs[:15]:
    print(" ", u[:120])
maps = sorted(set(re.findall(r'https?://[^"\']+(?:arcgis|hub|data\.surrey)[^"\']*', html2, re.I)))
for u in maps[:10]:
    print(" link:", u[:120])
