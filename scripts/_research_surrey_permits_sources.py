"""Research Surrey building permit data sources — URLs, formats, record counts."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from urllib.parse import urljoin

import requests

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"


def get_json(url: str, **params) -> dict | list:
    r = SESSION.get(url, params=params or None, timeout=90)
    r.raise_for_status()
    return r.json()


def count_csv_rows(url: str, *, limit_probe: int = 0) -> int | str:
    try:
        r = SESSION.get(url, timeout=180, stream=True)
        r.raise_for_status()
        if url.lower().endswith(".zip"):
            z = zipfile.ZipFile(io.BytesIO(r.content))
            names = z.namelist()
            total = 0
            for name in names:
                if not name.lower().endswith(".csv"):
                    continue
                with z.open(name) as handle:
                    text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
                    reader = csv.reader(text)
                    next(reader, None)
                    total += sum(1 for _ in reader)
            return total
        text = r.text
        reader = csv.reader(io.StringIO(text))
        next(reader, None)
        if limit_probe:
            return sum(1 for i, _ in enumerate(reader) if i < limit_probe)
        return sum(1 for _ in reader)
    except Exception as exc:
        return f"error: {exc}"


def arcgis_count(base: str) -> int | str:
    try:
        payload = get_json(f"{base}/query", where="1=1", returnCountOnly="true", f="json")
        if payload.get("error"):
            return f"error: {payload['error']}"
        return int(payload.get("count") or 0)
    except Exception as exc:
        return f"error: {exc}"


def main() -> None:
    print("=" * 70)
    print("1. CKAN — data.surrey.ca package_show building-permits")
    print("=" * 70)
    pkg = get_json(
        "https://data.surrey.ca/api/3/action/package_show",
        id="building-permits",
    )["result"]
    print("title:", pkg.get("title"))
    print("notes:", (pkg.get("notes") or "")[:300].replace("\n", " "))
    print("metadata_modified:", pkg.get("metadata_modified"))
    for res in pkg.get("resources", []):
        print("\n  resource:", res.get("name"))
        print("    format:", res.get("format"))
        print("    url:", res.get("url"))
        print("    size:", res.get("size"))
        print("    last_modified:", res.get("last_modified"))
        print("    datastore_active:", res.get("datastore_active"))

    print("\n" + "=" * 70)
    print("2. CKAN search — all permit-related datasets")
    print("=" * 70)
    for q in ["building permit", "issued building", "building permits"]:
        sr = get_json(
            "https://data.surrey.ca/api/3/action/package_search",
            q=q,
            rows=20,
        )["result"]
        print(f"\nquery={q!r} count={sr.get('count')}")
        for ds in sr.get("results", []):
            print(f"  - {ds.get('name')}: {ds.get('title')}")

    print("\n" + "=" * 70)
    print("3. Current ArcGIS FeatureServer (scraper endpoint)")
    print("=" * 70)
    current = (
        "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
        "IssuedBuildingPermits/FeatureServer/0"
    )
    meta = get_json(current, f="pjson")
    print("name:", meta.get("name"))
    print("description:", (meta.get("description") or "")[:200])
    print("maxRecordCount:", meta.get("maxRecordCount"))
    print("fields:", [f["name"] for f in meta.get("fields", [])])
    print("count 1=1:", arcgis_count(current))

    print("\n" + "=" * 70)
    print("4. ArcGIS Hub / alternate Surrey GIS endpoints")
    print("=" * 70)
    candidates = [
        "https://gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer?f=pjson",
        "https://gisservices.surrey.ca/arcgis/rest/services?f=pjson",
        "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services?f=pjson",
    ]
    for url in candidates:
        try:
            data = get_json(url)
            if "services" in data:
                hits = [
                    s["name"]
                    for s in data.get("services", [])
                    if any(x in s.get("name", "").lower() for x in ("permit", "building"))
                ]
                print(f"\n{url}")
                print("  permit-related services:", hits[:20] or "(none)")
            if "layers" in data:
                hits = [
                    l["name"]
                    for l in data.get("layers", [])
                    if any(x in l.get("name", "").lower() for x in ("permit", "building"))
                ]
                print(f"\n{url}")
                print("  permit-related layers:", hits[:20] or "(none)")
        except Exception as exc:
            print(f"\n{url}\n  error: {exc}")

    # Search services5 folder for other permit layers
    root = get_json(
        "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services",
        f="pjson",
    )
    permit_services = [
        s for s in root.get("services", [])
        if "permit" in s.get("name", "").lower() or "building" in s.get("name", "").lower()
    ]
    print("\nservices5 permit/building services:")
    for svc in permit_services:
        name = svc["name"]
        typ = svc["type"]
        base = f"https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/{name}/{typ}/0"
        print(f"  {name} ({typ})")
        try:
            layer = get_json(base, f="pjson")
            print(f"    fields: {[f['name'] for f in layer.get('fields', [])][:12]}")
            print(f"    count: {arcgis_count(base)}")
        except Exception as exc:
            print(f"    error: {exc}")

    print("\n" + "=" * 70)
    print("5. Downloadable resource row counts (where reachable)")
    print("=" * 70)
    for res in pkg.get("resources", []):
        url = res.get("url") or ""
        fmt = (res.get("format") or "").upper()
        if fmt not in {"CSV", "ZIP", "GEOJSON", "KML", "SHP"} and not url.lower().endswith((".csv", ".zip")):
            continue
        print(f"\n  {res.get('name')} [{fmt}]")
        print(f"    {url}")
        if fmt in {"CSV", "ZIP"} or url.lower().endswith((".csv", ".zip")):
            print(f"    rows: {count_csv_rows(url)}")

    print("\n" + "=" * 70)
    print("6. Development statistics page links")
    print("=" * 70)
    try:
        html = SESSION.get(
            "https://www.surrey.ca/renovating-building-development/land-planning-development/development-statistics",
            timeout=60,
        ).text
        for needle in ["data.surrey.ca", "arcgis", "building-permit", "open-data", "hub.arcgis"]:
            if needle.lower() in html.lower():
                print(f"  page mentions: {needle}")
    except Exception as exc:
        print(f"  error: {exc}")


if __name__ == "__main__":
    main()
