#!/usr/bin/env python3
"""Audit tender category distribution before/after resolve_tender_category."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scraper.tender_category import resolve_tender_category


def load_rows_from_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_rows_from_api(base_url: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    offset = 0
    limit = 500
    total = float("inf")

    while offset < total:
        query = urllib.parse.urlencode({"limit": limit, "offset": offset})
        url = f"{base_url.rstrip('/')}/api/tenders?{query}"
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.load(response)
        total = payload["total"]
        batch = payload.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < limit:
            break

    return rows


def summarize(rows: list[dict[str, str]], *, use_resolved: bool) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        if use_resolved:
            category = resolve_tender_category(
                title=row.get("title", ""),
                source=row.get("source", ""),
                raw_category=row.get("category", ""),
            )
        else:
            category = row.get("category", "") or "Uncategorized"
        counts[category] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("tenders.csv"),
        help="Local tenders.csv path (default: tenders.csv)",
    )
    parser.add_argument(
        "--api",
        default=os.environ.get("TENDERSCOPE_API", ""),
        help="API base URL (e.g. https://bc-tender-scraper-production.up.railway.app)",
    )
    args = parser.parse_args()

    if args.api:
        rows = load_rows_from_api(args.api)
        source_label = args.api
    else:
        rows = load_rows_from_csv(args.csv)
        source_label = str(args.csv)

    before = summarize(rows, use_resolved=False)
    after = summarize(rows, use_resolved=True)

    changed = 0
    for row in rows:
        old = row.get("category", "") or "Uncategorized"
        new = resolve_tender_category(
            title=row.get("title", ""),
            source=row.get("source", ""),
            raw_category=row.get("category", ""),
        )
        if old != new:
            changed += 1

    print(f"Source: {source_label}")
    print(f"Rows: {len(rows)}")
    print(f"Changed category: {changed}")
    print(f"Before: {dict(before)}")
    print(f"After:  {dict(after)}")
    print(f"Expected Services after next sync: {after.get('Services', 0)}")


if __name__ == "__main__":
    main()
