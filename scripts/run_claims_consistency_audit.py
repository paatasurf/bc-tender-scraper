#!/usr/bin/env python3
"""Class A — no write. Classification Claims cross-table consistency audit.

Read-only introspection over classification_claims / claim_evidence /
claim_events / rule_set_versions checking invariants the schema's own FK/
CHECK constraints cannot express:

- every claim's primary_evidence_content_hash matches an actual
  claim_evidence row for that claim;
- every claim has at most one terminal event (uq_claim_events_one_per_claim
  should already guarantee this -- re-verified here as a Gateway-bug/
  direct-SQL/manual-operation drift detector);
- every superseded event's related_claim_id shares (company_id, claim_type,
  predicate) with its own claim;
- every claim's and event's rule_set_version_id is compatible with its own
  claim_type and already effective at its own timestamp;
- idempotency_key / primary_evidence_content_hash / content_hash are all
  well-formed lowercase SHA-256 hex digests;
- no dangling claim_id/related_claim_id/rule_set_version_id references.

Target database: DATABASE_URL (local) by default; pass --use-production to
read DATABASE_URL_PRODUCTION instead. Class A read-only in either case --
no --allow-production, no interactive destructive confirmation, no DDL/DML.

Prints JSON to stdout. Guard banner goes to stderr only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401  (loads .env before importing db.connection)
from db.classification_claims_consistency_audit import run_claims_consistency_audit
from db.connection import get_engine
from db.db_safety import guard_readonly_db_from_args

_SCRIPT = Path(__file__).name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-production",
        action="store_true",
        help="Read DATABASE_URL_PRODUCTION instead of local DATABASE_URL (Class A read-only; banner only).",
    )
    args = parser.parse_args()

    with contextlib.redirect_stdout(sys.stderr):
        guard_readonly_db_from_args(args, script_name=_SCRIPT)

    # run_claims_consistency_audit takes the Engine, not a Session, so it can
    # own its own REPEATABLE READ transaction for all four SELECTs -- see its
    # module docstring.
    engine = get_engine()
    output = run_claims_consistency_audit(engine)

    print(json.dumps(output, indent=2, default=str))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
