"""Class A — no write. Registry Engine Stage 2A: Evidence Link readiness audit.

Read-only report over permits.company_id / contract_awards.company_id
(activity Evidence Links) plus the tenders.company_id schema-gap check.
Prints JSON to stdout. Does not create, fix, or persist anything.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401  (loads .env before importing db.connection)
from db.connection import get_session_factory
from pipeline.registry_engine.evidence import (
    audit_contract_award_evidence_links,
    audit_permit_evidence_links,
    audit_tender_evidence_linkage,
)


def main() -> int:
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
