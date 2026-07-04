#!/usr/bin/env python3
"""Verify P1-02 tender presence tracking across consecutive imports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.import_csv import import_all_csvs, _read_csv
from db.models import ArchTender, CommercialTender, Tender
from scraper.config import ARCH_TENDERS_CSV, COMMERCIAL_TENDERS_CSV, OUTPUT_CSV
from sqlalchemy import func, select


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _table_counts(session) -> dict[str, int]:
    return {
        "tenders": session.scalar(select(func.count()).select_from(Tender)) or 0,
        "commercial_tenders": session.scalar(select(func.count()).select_from(CommercialTender)) or 0,
        "arch_tenders": session.scalar(select(func.count()).select_from(ArchTender)) or 0,
    }


def _sample_present_rows(session, model, csv_path: str, limit: int = 3) -> list[dict]:
    urls = [row.get("url", "") for row in _read_csv(Path(csv_path)) if row.get("url")][:limit]
    if not urls:
        return []
    rows = session.scalars(select(model).where(model.url.in_(urls))).all()
    rows.sort(key=lambda row: row.id)
    return [
        {
            "id": row.id,
            "url": getattr(row, "url", ""),
            "title": getattr(row, "title", "")[:80],
            "first_seen_at": _iso(row.first_seen_at),
            "last_seen_at": _iso(row.last_seen_at),
            "updated_at": _iso(row.updated_at),
        }
        for row in rows
    ]


def _capture_snapshot(session) -> dict:
    return {
        "counts": _table_counts(session),
        "samples": {
            "federal": _sample_present_rows(session, Tender, OUTPUT_CSV),
            "commercial": _sample_present_rows(session, CommercialTender, COMMERCIAL_TENDERS_CSV),
            "arch": _sample_present_rows(session, ArchTender, ARCH_TENDERS_CSV),
        },
    }


def _compare_runs(before: dict, after: dict) -> dict:
    checks = {
        "counts_unchanged": before["counts"] == after["counts"],
        "first_seen_preserved": True,
        "last_seen_advanced": True,
        "updated_at_stable_when_unchanged": True,
        "violations": [],
    }

    for table_key in ("federal", "commercial", "arch"):
        before_by_id = {row["id"]: row for row in before["samples"][table_key]}
        after_by_id = {row["id"]: row for row in after["samples"][table_key]}
        for row_id, prev in before_by_id.items():
            curr = after_by_id.get(row_id)
            if curr is None:
                checks["first_seen_preserved"] = False
                checks["violations"].append(f"{table_key} id={row_id} missing after second import")
                continue
            if curr["first_seen_at"] != prev["first_seen_at"]:
                checks["first_seen_preserved"] = False
                checks["violations"].append(
                    f"{table_key} id={row_id} first_seen_at changed "
                    f"{prev['first_seen_at']} -> {curr['first_seen_at']}"
                )
            if curr["last_seen_at"] is None or prev["last_seen_at"] is None:
                checks["last_seen_advanced"] = False
                checks["violations"].append(f"{table_key} id={row_id} last_seen_at is null")
            elif curr["last_seen_at"] <= prev["last_seen_at"]:
                checks["last_seen_advanced"] = False
                checks["violations"].append(
                    f"{table_key} id={row_id} last_seen_at did not advance "
                    f"{prev['last_seen_at']} -> {curr['last_seen_at']}"
                )
            if curr["updated_at"] != prev["updated_at"]:
                checks["updated_at_stable_when_unchanged"] = False
                checks["violations"].append(
                    f"{table_key} id={row_id} updated_at changed without expected content edit "
                    f"{prev['updated_at']} -> {curr['updated_at']}"
                )

    checks["pass"] = all(
        checks[key]
        for key in (
            "counts_unchanged",
            "first_seen_preserved",
            "last_seen_advanced",
            "updated_at_stable_when_unchanged",
        )
    )
    return checks


def main() -> int:
    guard_readonly_db(_SCRIPT)
    session = get_session()
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    try:
        report["before_migration_counts"] = _table_counts(session)
        report["baseline"] = _capture_snapshot(session)

        print("[Verify] Import pass 1...")
        import_all_csvs(session)
        report["after_pass_1"] = _capture_snapshot(session)

        print("[Verify] Import pass 2...")
        import_all_csvs(session)
        report["after_pass_2"] = _capture_snapshot(session)

        report["checks"] = _compare_runs(report["after_pass_1"], report["after_pass_2"])
        report["migration_counts_unchanged"] = (
            report["before_migration_counts"] == report["after_pass_2"]["counts"]
        )
        report["pass"] = report["checks"]["pass"] and report["migration_counts_unchanged"]
    finally:
        session.close()

    out_dir = Path(__file__).resolve().parent.parent / ".pipeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "p1-02-presence-verification.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[Verify] Wrote {out_path}")
    print(f"[Verify] pass={report.get('pass')}")
    if not report.get("pass"):
        print(f"[Verify] violations={report.get('checks', {}).get('violations', [])}")
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
