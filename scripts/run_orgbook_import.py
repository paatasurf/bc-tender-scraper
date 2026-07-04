"""CLI: import OrgBook reference data into orgbook_reference."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.connection import get_session, init_db
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args
from pipeline.registry_verification.orgbook_import import import_orgbook_reference

_SCRIPT = Path(__file__).name


def main() -> None:
    parser = argparse.ArgumentParser(description="Import BC OrgBook reference export")
    add_production_safety_args(parser)
    parser.add_argument("path", help="Path to OrgBook CSV or JSONL export")
    args = parser.parse_args()

    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="orgbook import")

    init_db()
    session = get_session()
    try:
        result = import_orgbook_reference(session, args.path)
        print(result)
    finally:
        session.close()


if __name__ == "__main__":
    main()
