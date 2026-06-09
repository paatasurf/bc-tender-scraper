#!/usr/bin/env python3
"""Import existing CSV files into PostgreSQL."""

import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.import_csv import import_all_csvs


def main() -> None:
    init_db()
    session = get_session()
    try:
        counts = import_all_csvs(session)
        print(f"Import complete: {counts}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
