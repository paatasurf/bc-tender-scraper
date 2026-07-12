#!/usr/bin/env python3
"""P1/P2 staging gate validation via internal API (Constitution-compliant).

Does NOT connect to DATABASE_URL directly. Uses X-Internal-Key against the
deployed TenderScope API (staging or production worker with flags set in Railway).

Required env (in .env or shell):
  TENDERSCOPE_API_URL  — e.g. https://your-app.up.railway.app
  INTERNAL_API_KEY

Railway staging must have:
  KG_OBSERVATION_DUAL_WRITE=1
  KG_GATEWAY_SHADOW=1
  KG_GATEWAY_ENFORCE=0   (shadow soak only)
  ALLOW_MANUAL_PIPELINE=true

Usage:
  python scripts/run_kg_staging_gate_validation.py
  python scripts/run_kg_staging_gate_validation.py --skip-cycles
  python scripts/run_kg_staging_gate_validation.py --local-p1-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    import config.env  # noqa: F401


def _api_base() -> str:
    _load_env()
    base = (
        os.getenv("TENDERSCOPE_API_URL", "").strip()
        or os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    )
    if base and not base.startswith("http"):
        base = f"https://{base}"
    return base.rstrip("/")


def _internal_key() -> str:
    _load_env()
    return os.getenv("INTERNAL_API_KEY", "").strip()


def _request(method: str, path: str, *, timeout: int = 600) -> dict[str, Any]:
    base = _api_base()
    key = _internal_key()
    if not base or not key:
        raise SystemExit("Set TENDERSCOPE_API_URL and INTERNAL_API_KEY")

    url = f"{base}{path}"
    req = Request(url, method=method, headers={"X-Internal-Key": key})
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            data = json.loads(body) if body else {}
            if isinstance(data, dict):
                data["_client_elapsed_ms"] = elapsed_ms
            return data
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {path}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"Request failed {path}: {exc}") from exc


def _post(path: str, *, timeout: int = 600) -> dict[str, Any]:
    return _request("POST", path, timeout=timeout)


def _get(path: str) -> dict[str, Any]:
    return _request("GET", path, timeout=120)


def _poll_step(pipeline_run_id: int, *, timeout_s: int = 900) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        payload = _get(f"/internal/steps/{pipeline_run_id}")
        if payload.get("done"):
            return payload
        time.sleep(5)
    raise SystemExit(f"Timed out polling pipeline_run_id={pipeline_run_id}")


def _run_local_p1_validation() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "scripts/run_kg_phase1_validation.py", "--dual-write-smoke"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
    report["returncode"] = proc.returncode
    return report


def run_staging_gate(*, skip_cycles: bool = False, local_p1_only: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "local_p1_only" if local_p1_only else "api_staging",
        "gates": {},
    }

    if local_p1_only:
        report["p1_local"] = _run_local_p1_validation()
        report["all_ok"] = report["p1_local"].get("all_ok", False)
        return report

    snapshot_before = _get("/internal/kg/validation-snapshot")
    report["snapshot_before"] = snapshot_before

    flags = snapshot_before.get("flags", {})
    report["gates"]["p1_dual_write_flag"] = flags.get("KG_OBSERVATION_DUAL_WRITE") is True
    report["gates"]["p2_shadow_flag"] = flags.get("KG_GATEWAY_SHADOW") is True
    report["gates"]["p2_enforce_off"] = flags.get("KG_GATEWAY_ENFORCE") is False
    report["gates"]["schema_ok"] = snapshot_before.get("schema_ok") is True

    cycles: dict[str, Any] = {}
    if not skip_cycles:
        permit_start = _post("/internal/scrape/building-permits")
        cycles["permits"] = {
            "start": permit_start,
            "poll": _poll_step(int(permit_start["pipeline_run_id"])),
        }

        awards_start = _post("/internal/scrape/contract-awards")
        cycles["awards_import"] = {
            "start": awards_start,
            "poll": _poll_step(int(awards_start["pipeline_run_id"])),
        }

        award_populate = _post("/internal/kg/populate-award-companies?sync=true&dry_run=true")
        cycles["award_populate_dry_run"] = award_populate

    report["cycles"] = cycles

    snapshot_after = _get("/internal/kg/validation-snapshot")
    report["snapshot_after"] = snapshot_after

    obs_before = snapshot_before.get("observations", {}).get("total", 0)
    obs_after = snapshot_after.get("observations", {}).get("total", 0)
    dec_before = snapshot_before.get("decisions", {}).get("total", 0)
    dec_after = snapshot_after.get("decisions", {}).get("total", 0)

    report["deltas"] = {
        "observations": obs_after - obs_before,
        "decisions": dec_after - dec_before,
    }

    permit_poll = (cycles.get("permits") or {}).get("poll") or {}
    awards_poll = (cycles.get("awards_import") or {}).get("poll") or {}
    report["regressions"] = {
        "permit_step_failed": permit_poll.get("status") == "failed",
        "awards_import_failed": awards_poll.get("status") == "failed",
        "unexpected_enforce_blocks": snapshot_after.get("decisions", {}).get(
            "unexpected_enforce_blocks", []
        ),
    }

    report["performance"] = {
        "snapshot_before_ms": snapshot_before.get("snapshot_ms"),
        "snapshot_after_ms": snapshot_after.get("snapshot_ms"),
        "permit_run_duration_ms": (permit_poll.get("duration_ms") if isinstance(permit_poll, dict) else None),
    }

    report["all_ok"] = (
        report["gates"]["schema_ok"]
        and report["gates"]["p1_dual_write_flag"]
        and report["gates"]["p2_shadow_flag"]
        and report["gates"]["p2_enforce_off"]
        and not report["regressions"]["permit_step_failed"]
        and not report["regressions"]["awards_import_failed"]
        and (skip_cycles or report["deltas"]["observations"] >= 0)
    )

    report["recommendation"] = (
        "Continue shadow soak; do NOT enable KG_GATEWAY_ENFORCE until "
        "decision audit aligns with legacy creates and no regressions for one release cycle."
        if report["all_ok"]
        else "Fix failing gates before P3; keep KG_GATEWAY_ENFORCE=0."
    )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="KG P1/P2 staging gate validation")
    parser.add_argument("--skip-cycles", action="store_true", help="Snapshot + flags only")
    parser.add_argument(
        "--local-p1-only",
        action="store_true",
        help="Run local P1 script only (requires local DATABASE_URL)",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write JSON report to file (e.g. exports/kg_staging_gate_report.json)",
    )
    args = parser.parse_args()
    report = run_staging_gate(skip_cycles=args.skip_cycles, local_p1_only=args.local_p1_only)
    payload = json.dumps(report, indent=2)
    print(payload)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
    if not report.get("all_ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
