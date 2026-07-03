"""CLI: import OrgBook reference data into orgbook_reference."""

from __future__ import annotations

import argparse

from db.connection import get_session, init_db
from pipeline.registry_verification.orgbook_import import import_orgbook_reference


def main() -> None:
    parser = argparse.ArgumentParser(description="Import BC OrgBook reference export")
    parser.add_argument("path", help="Path to OrgBook CSV or JSONL export")
    args = parser.parse_args()

    init_db()
    session = get_session()
    try:
        result = import_orgbook_reference(session, args.path)
        print(result)
    finally:
        session.close()


if __name__ == "__main__":
    main()
