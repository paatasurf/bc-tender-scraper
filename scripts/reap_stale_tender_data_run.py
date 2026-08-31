#!/usr/bin/env python3
"""Manually reclaim a stale/orphaned tender_data coordinator run (Class C).

Standalone operator script -- deliberately NOT wired into any endpoint,
cron, or deploy step. pipeline.run_coordinator_postgres.reap_stale_run()
already gets called automatically as a side effect of begin_run()/
begin_or_resume_run() (the next time someone tries to start a tender_data
run) or of a late worker callback landing after its own lease expired
(_mutate_locked_run) -- this script exists for the gap those two paths
don't cover: proactively reclaiming an expired-lease run *without* waiting
for either of those to happen next, e.g. to clear an ops dashboard's
`expired_lease_run` (see pipeline/ops_read_model.py::get_coordinator_summary)
between scheduled runs, or to unblock a manual retrigger sooner than the
next cron tick.

Never reclaims a live run: the only criterion is
pipeline_coordinator_runs.lease_expires_at, the same heartbeat every
mutating coordinator call already renews on every phase transition. A run
still making progress renews its own lease and can never be reclaimed
here, no matter how long it has run or how little of the
scrape/import lifecycle it has completed.

--dry-run (default): read-only report of the current active row (if any)
and whether its lease has expired -- makes no changes.
--apply: calls reap_stale_run(). Idempotent and safe to rerun -- if
nothing is stale (no active row, or the active row's lease hasn't expired
yet), it is a reported no-op, not an error.

This is a Class C registry write (not schema DDL): it updates at most one
existing pipeline_coordinator_runs row's status/phase/error columns,
never touches DDL, never deletes a row. See db/classification.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401,E402

from config.env import get_env  # noqa: E402
from db.classification import SafetyClass  # noqa: E402
from db.db_safety import add_production_safety_args, begin_script_guard  # noqa: E402

_SCRIPT = Path(__file__).name


def _require_postgres_backend() -> None:
    backend = get_env("PIPELINE_COORDINATOR_BACKEND", "legacy").strip().lower()
    if backend != "postgres":
        print(
            f"[reap_stale_tender_data_run] PIPELINE_COORDINATOR_BACKEND={backend!r} "
            "-- this script only applies to the postgres backend (the legacy "
            "JSON+threading.Lock backend has no lease/TTL concept to reclaim "
            "against). Nothing to do.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _build_report() -> dict:
    from pipeline.run_coordinator_postgres import _SCOPE, describe_active_run

    active = describe_active_run()
    if active is None:
        return {
            "operation": "reap_stale_tender_data_run",
            "class": "C",
            "dry_run": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": _SCOPE,
            "active_run": None,
            "would_reclaim": False,
            "reason": "no_active_run",
        }
    return {
        "operation": "reap_stale_tender_data_run",
        "class": "C",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": _SCOPE,
        "active_run": {
            "run_id": active["run_id"],
            "phase": active["phase"],
            "lease_expires_at": active["lease_expires_at"],
        },
        "would_reclaim": active["lease_expired"],
        "reason": "lease_expired" if active["lease_expired"] else "lease_still_valid",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_production_safety_args(parser)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually reclaim the run if its lease has expired (default is --dry-run).",
    )
    args = parser.parse_args()

    _require_postgres_backend()

    if args.apply:
        begin_script_guard(
            SafetyClass.C,
            _SCRIPT,
            allow_production=args.allow_production,
        )
        from pipeline.run_coordinator_postgres import reap_stale_run

        result = reap_stale_run()
        print(json.dumps(result, indent=2, default=str))
        if result["reclaimed"]:
            print(
                f"\n[reap_stale_tender_data_run] Reclaimed stale run "
                f"{result['run_id']!r} (lease expired at {result['lease_expires_at']})."
            )
        else:
            print(f"\n[reap_stale_tender_data_run] No-op: {result['reason']}.")
        return

    begin_script_guard(
        SafetyClass.A,
        _SCRIPT,
        use_production=args.use_production,
    )
    report = _build_report()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
