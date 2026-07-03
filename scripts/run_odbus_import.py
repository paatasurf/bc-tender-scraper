#!/usr/bin/env python3
"""Import Statistics Canada ODB CSV into odbus_reference."""

from __future__ import annotations

import argparse

import config.env  # noqa: F401

from db.connection import get_session, init_db
from pipeline.registry_verification.odbus_import import import_odbus_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ODBus_v1.csv into odbus_reference.")
    parser.add_argument(
        "csv_path",
        help="Path to ODBus_v1.csv (from ODBus_2023.zip).",
    )
    args = parser.parse_args()

    init_db()
    session = get_session()
    try:
        results = import_odbus_csv(session, args.csv_path)
    finally:
        session.close()

    print("[ODB Import] Done:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
