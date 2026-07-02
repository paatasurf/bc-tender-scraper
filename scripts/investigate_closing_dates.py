#!/usr/bin/env python3
"""Read-only investigation for P2-06 closing_at backfill (Step 1)."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def classify_format(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return "EMPTY"
    if re.fullmatch(r"\d{4}/\d{2}/\d{2}", s):
        return "YYYY/MM/DD"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return "YYYY-MM-DD"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s):
        return "YYYY-MM-DD HH:MM"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", s):
        return "ISO_T"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", s):
        return "ISO_TZ"
    if re.match(r"^[A-Za-z]+ \d{1,2}, \d{4}", s):
        return "Month D, YYYY"
    if re.search(r"day\(s\) left", s, re.I):
        return "status_not_date"
    if re.search(r" at \d{2}:\d{2} pt", s, re.I):
        return "YYYY-MM-DD at HH:MM pt (noisy)"
    if re.search(r"\d{1,2}/\d{1,2}/\d{4}", s):
        return "slash_date"
    if s.lower() in {"not available", "n/a", "tbd", "tba"}:
        return "SENTINEL_NOT_DATE"
    if re.search(r"\d{4}", s):
        return "OTHER_WITH_YEAR"
    return "OTHER"


LIKELY_PARSEABLE = {
    "YYYY/MM/DD",
    "YYYY-MM-DD",
    "YYYY-MM-DD HH:MM",
    "ISO_T",
    "ISO_TZ",
    "Month D, YYYY",
    "YYYY-MM-DD at HH:MM pt (noisy)",
    "slash_date",
}


def summarize_values(label: str, rows: list[tuple[str, str | None, str | None]]) -> dict:
    fmt = Counter()
    samples: dict[str, list] = defaultdict(list)
    closing_at_set = 0
    for raw, closing_at, source in rows:
        f = classify_format(raw or "")
        fmt[f] += 1
        if closing_at is not None:
            closing_at_set += 1
        if len(samples[f]) < 3 and (raw or "").strip():
            samples[f].append({"value": (raw or "").strip(), "source": source})

    total = len(rows)
    non_empty = sum(count for key, count in fmt.items() if key != "EMPTY")
    parseable = sum(count for key, count in fmt.items() if key in LIKELY_PARSEABLE)
    unparseable_nonempty = sum(
        count for key, count in fmt.items() if key not in {"EMPTY"} and key not in LIKELY_PARSEABLE
    )

    return {
        "label": label,
        "total_rows": total,
        "closing_at_already_set": closing_at_set,
        "non_empty_raw": non_empty,
        "empty_raw": fmt.get("EMPTY", 0),
        "likely_parseable": parseable,
        "unparseable_nonempty": unparseable_nonempty,
        "would_stay_null": fmt.get("EMPTY", 0) + unparseable_nonempty,
        "format_counts": dict(fmt.most_common()),
        "samples": samples,
    }


def analyze_csv(path: Path, column: str, label: str) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    payload = [(row.get(column, ""), None, row.get("source")) for row in rows]
    return summarize_values(f"CSV {label}", payload)


def analyze_db() -> list[dict]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return []

    engine = create_engine(database_url)
    specs = [
        ("tenders", "closing_date", "source"),
        ("commercial_tenders", "deadline", "source"),
        ("arch_tenders", "deadline", None),
    ]
    out = []
    with engine.connect() as conn:
        for table, column, source_col in specs:
            if source_col:
                result = conn.execute(text(f"SELECT {column}, closing_at, {source_col} FROM {table}"))
            else:
                result = conn.execute(text(f"SELECT {column}, closing_at, NULL::text AS source FROM {table}"))
            rows = [(raw, closing_at, source) for raw, closing_at, source in result]
            out.append(summarize_values(f"DB {table}.{column}", rows))
    return out


def main() -> None:
    report = {
        "csv": [
            analyze_csv(ROOT / "tenders.csv", "closing_date", "tenders.closing_date"),
            analyze_csv(ROOT / "commercial_tenders.csv", "deadline", "commercial_tenders.deadline"),
            analyze_csv(ROOT / "arch_tenders.csv", "deadline", "arch_tenders.deadline"),
        ],
        "database": analyze_db(),
    }
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            report["commercial_empty_deadline_by_source"] = [
                {"source": row[0], "count": row[1]}
                for row in conn.execute(
                    text(
                        """
                        SELECT COALESCE(NULLIF(TRIM(source), ''), '(blank)') AS source, COUNT(*)
                        FROM commercial_tenders
                        WHERE deadline IS NULL OR TRIM(deadline) = ''
                        GROUP BY 1
                        ORDER BY 2 DESC
                        """
                    )
                )
            ]
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
