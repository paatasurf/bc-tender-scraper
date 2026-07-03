#!/usr/bin/env python3
"""Match canonical companies to ODB reference records."""

from __future__ import annotations

import argparse

import config.env  # noqa: F401

from db.connection import get_session, init_db
from pipeline.registry_verification.odbus_match import match_odbus_for_companies


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match canonical companies to ODB (never creates companies).",
    )
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
