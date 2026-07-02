#!/usr/bin/env python3
"""Audit script for P1-01 pipeline ordering.

Runs the deterministic tender-data pipeline and writes an audit JSON file
proving import cannot start before tender scrapes finish.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config.env  # noqa: F401

from pipeline.run_coordinator import (
    PipelineOrderError,
    assert_import_not_before_scrape,
    assert_ready_for_import,
    get_run_state,
)
from pipeline.tender_data_pipeline import run_tender_data_pipeline


def main() -> int:
    audit: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pre_run_gate": {},
        "pipeline_summary": {},
        "ordering_audit": {},
        "coordinator_state": {},
        "ordering_pass": False,
        "pipeline_pass": False,
    }

    print("[Audit] Pre-run gate: import must be blocked before scrapes")
    state_path = Path(__file__).resolve().parent.parent / ".pipeline" / "run_coordinator.json"
    if state_path.exists():
        state_path.unlink()
    try:
        assert_ready_for_import(None)
        audit["pre_run_gate"] = {"blocked": False, "error": None}
        print("[Audit] FAIL: import was not blocked on a fresh coordinator state")
    except PipelineOrderError as exc:
        audit["pre_run_gate"] = {"blocked": True, "error": str(exc)}
        print(f"[Audit] OK: {exc}")

    print("[Audit] Running full tender-data pipeline...")
    try:
        summary = run_tender_data_pipeline()
        audit["pipeline_summary"] = {
            "status": summary.get("status"),
            "run_id": summary.get("run_id"),
            "phases": list(summary.get("phases", {}).keys()),
        }
    except Exception as exc:
        audit["pipeline_summary"] = {
            "status": "failed",
            "error": str(exc)[:2000],
        }
        _write_audit(audit)
        print(f"[Audit] Pipeline failed: {exc}")
        return 1

    ordering = assert_import_not_before_scrape()
    audit["ordering_audit"] = ordering
    state = get_run_state()
    audit["coordinator_state"] = state.to_dict() if state else {}

    scrape_finished = ordering.get("tender_scrape_finished_at")
    import_started = ordering.get("import_started_at")
    ordering_ok = ordering.get("ordering_ok") == "True"

    print("[Audit] Ordering timestamps:")
    print(f"  tender_scrape_finished_at: {scrape_finished}")
    print(f"  import_started_at:         {import_started}")
    print(f"  ordering_ok:               {ordering_ok}")

    audit["ordering_pass"] = (
        audit["pre_run_gate"].get("blocked") is True
        and ordering_ok
        and scrape_finished is not None
        and import_started is not None
        and import_started >= scrape_finished
    )
    audit["pipeline_pass"] = audit["pipeline_summary"].get("status") == "success"
    audit["pass"] = audit["ordering_pass"]
    _write_audit(audit)

    if audit["ordering_pass"]:
        print("[Audit] PASS: imports cannot start before tender scrapes finish")
    else:
        print("[Audit] FAIL: import ordering not proven")

    if not audit["pipeline_pass"]:
        print(
            "[Audit] NOTE: full pipeline did not complete successfully "
            f"({audit['pipeline_summary'].get('error', 'unknown')})"
        )
        if audit["ordering_pass"]:
            return 0

    return 0 if audit["pass"] else 1


def _write_audit(audit: dict) -> None:
    out_path = Path(__file__).resolve().parent.parent / ".pipeline" / "p1-01-ordering-audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(f"[Audit] Wrote {out_path}")


if __name__ == "__main__":
    raise SystemExit(main())
