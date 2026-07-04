#!/usr/bin/env python3
"""Recompute deterministic construction tiers for all companies."""

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
from pipeline.construction_tier import compute_construction_tiers

_SCRIPT = Path(__file__).name


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute construction tiers (A–E).")
    add_production_safety_args(parser)
    parser.add_argument(
        "--company-id",
        type=int,
        action="append",
        dest="company_ids",
        help="Limit to specific company id (repeatable).",
    )
    args = parser.parse_args()

    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="construction tier recompute")

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
