#!/usr/bin/env python3
"""Create net-new company rows from unmatched contract award vendors (Phase B)."""

from __future__ import annotations

import argparse

import config.env  # noqa: F401

from db.connection import get_session, init_db
from pipeline.populate_companies_from_awards import populate_companies_from_awards


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Insert net-new companies from unmatched contract_awards vendors.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without inserting rows.",
    )
    args = parser.parse_args()

    init_db()
    session = get_session()
    try:
        results = populate_companies_from_awards(session, dry_run=args.dry_run)
    finally:
        session.close()

    print("[AwardCompanies] Done:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
