"""Class A — no write. Registry Engine Stage 2A: Evidence Link readiness audit.

Read-only report over permits.company_id / contract_awards.company_id
(activity Evidence Links) plus the tenders.company_id schema-gap check.
Prints JSON to stdout. Does not create, fix, or persist anything.
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
from pipeline.registry_engine.evidence import (
    audit_contract_award_evidence_links,
    audit_permit_evidence_links,
    audit_tender_evidence_linkage,
)

_SCRIPT = Path(__file__).name


def main() -> int:
    # Keep the db_safety banner off stdout so stdout stays pure JSON for
    # callers piping this script's output (e.g. `| jq`, `json.loads`).
    # db_safety.print_database_banner is untouched — it still prints to
    # whatever sys.stdout is at call time; this redirect is local to this
    # script's guard call only, not a change to db_safety.py itself.
    with contextlib.redirect_stdout(sys.stderr):
        guard_readonly_db(_SCRIPT)
    factory = get_session_factory()
    session = factory()
    try:
        permit_report = audit_permit_evidence_links(session)
        award_report = audit_contract_award_evidence_links(session)
        tender_report = audit_tender_evidence_linkage(session)
    finally:
        session.close()

    output = {
        "permits": asdict(permit_report),
        "contract_awards": asdict(award_report),
        "tenders": asdict(tender_report),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
