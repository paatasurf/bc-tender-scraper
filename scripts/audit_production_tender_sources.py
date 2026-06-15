"""Audit production tender sources (read-only)."""
from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BASE = "https://bc-tender-scraper-production.up.railway.app"
CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)


def get(path: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "tender-source-audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def fetch_all(endpoint: str, limit: int = 500) -> tuple[int, list[dict]]:
    rows: list[dict] = []
    offset = 0
    total = 0
    while True:
        page = get(f"{endpoint}?limit={limit}&offset={offset}")
        batch = page.get("data", [])
        total = int(page.get("total", len(batch)))
        rows.extend(batch)
        if not batch or len(rows) >= total:
            break
        offset += limit
        if offset > 5000:
            break
    return total, rows[:total]


def recent_scraped(rows: list[dict]) -> int:
    count = 0
    for row in rows:
        ts = parse_ts(row.get("scraped_at"))
        if ts and ts >= CUTOFF:
            count += 1
    return count


def main() -> None:
    stats = get("/api/stats")
    print("=== /api/stats ===")
    print(json.dumps(stats, indent=2))

    datasets = [
        ("federal_tenders", "/api/tenders", "source"),
        ("commercial_tenders", "/api/commercial-tenders", "source"),
        ("arch_tenders", "/api/arch-tenders", None),
    ]

    for label, endpoint, source_field in datasets:
        print(f"\n=== {label} ({endpoint}) ===")
        try:
            total, rows = fetch_all(endpoint)
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue
        print(f"total={total} fetched={len(rows)}")
        if source_field:
            print("by source:", dict(Counter(r.get(source_field, "") or "(empty)" for r in rows)))
        if rows:
            print("sample keys:", sorted(rows[0].keys()))
        print(f"scraped_at within 30d: {recent_scraped(rows)}/{len(rows)}")
        if rows:
            posted = Counter((r.get("posted_date") or r.get("deadline") or "")[:10] for r in rows)
            print("top posted/deadline dates:", posted.most_common(5))


if __name__ == "__main__":
    main()
