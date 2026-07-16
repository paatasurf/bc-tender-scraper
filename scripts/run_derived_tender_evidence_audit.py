"""Class A — no write. Derived Tender Evidence Link Readiness Audit.

Read-only report over two existing, unenforced paths from tenders to
companies:

  Path A: tenders.award_id -> contract_awards.id -> contract_awards.company_id
  Path B: tenders.tender_id -> tender_outcomes.tender_id -> tender_outcomes.company_id

Prints JSON to stdout. Does not create, fix, or persist anything — no
tenders.company_id, no FK, no index, no migration, no backfill.
"""

from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401  (loads .env before importing db.connection)
from db.connection import get_session_factory
from db.db_safety import guard_readonly_db
from pipeline.registry_engine.derived_tender_evidence import (
    run_derived_tender_evidence_audit,
)

_SCRIPT = Path(__file__).name


def main() -> int:
    # Keep the db_safety banner off stdout so stdout stays pure JSON for
    # callers piping this script's output. db_safety.print_database_banner
    # is untouched — this redirect is local to this script's guard call only.
    with contextlib.redirect_stdout(sys.stderr):
        guard_readonly_db(_SCRIPT)
    factory = get_session_factory()
    session = factory()
    try:
        report = run_derived_tender_evidence_audit(session)
    finally:
        session.close()

    output = {
        "path_a": asdict(report.path_a),
        "path_b": asdict(report.path_b),
        "cross_path": asdict(report.cross_path),
        "schema_version": report.schema_version,
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
