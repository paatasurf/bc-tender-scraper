#!/usr/bin/env python3
"""Match canonical companies to ODB reference records."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args
from pipeline.registry_verification.odbus_match import match_odbus_for_companies

_SCRIPT = Path(__file__).name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match canonical companies to ODB (never creates companies).",
    )
    add_production_safety_args(parser)
    parser.add_argument(
        "--company-id",
        type=int,
        action="append",
        dest="company_ids",
        help="Limit to specific canonical company id (repeatable).",
    )
    parser.add_argument(
        "--include-review-tiers",
        action="store_true",
        help="Include T4 family and T5 fuzzy matches as review_pending.",
    )
    args = parser.parse_args()

    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="registry verification match")

    init_db()
    session = get_session()
    try:
        results = match_odbus_for_companies(
            session,
            company_ids=args.company_ids,
            include_review_tiers=args.include_review_tiers,
        )
    finally:
        session.close()

    print("[Registry Verification] Done:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
