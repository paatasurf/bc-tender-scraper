#!/usr/bin/env python3
"""Recompute deterministic construction tiers for all companies."""

from __future__ import annotations

import argparse

import config.env  # noqa: F401

from db.connection import get_session, init_db
from pipeline.construction_tier import compute_construction_tiers


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute construction tiers (A–E).")
    parser.add_argument(
        "--company-id",
        type=int,
        action="append",
        dest="company_ids",
        help="Limit to specific company id (repeatable).",
    )
    args = parser.parse_args()

    init_db()
    session = get_session()
    try:
        results = compute_construction_tiers(session, company_ids=args.company_ids)
    finally:
        session.close()

    print("[Construction Tier] Done:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
