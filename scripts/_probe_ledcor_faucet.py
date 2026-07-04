"""Read-only probe: Ledcor name resolution and matching keys."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import check_db_connection, get_session
from db.db_safety import guard_readonly_db
from pipeline.company_canonical_merge import resolve_company_name
from pipeline.company_matching import normalize_vendor_name

_SCRIPT = Path(__file__).name


def main() -> None:
    guard_readonly_db(_SCRIPT)
    if not check_db_connection():
        raise SystemExit(1)

    session = get_session()
    try:
        parsed = resolve_company_name("New Person DBA: Ledcor")
        print("parsed:", parsed)
        if parsed:
            print("canonical_key:", parsed.canonical_key)
        rows = session.execute(
            text(
                """
                SELECT id, name, entity_role, display_name, canonical_company_id
                FROM companies
                WHERE name ILIKE '%ledcor%' OR display_name ILIKE '%ledcor%'
                ORDER BY id
                """
            )
        ).all()
        for row in rows:
            d = dict(row._mapping)
            key = normalize_vendor_name(d.get("display_name") or d.get("name") or "")
            print(d["id"], d["entity_role"], key, "|", d.get("display_name"), "|", d["name"][:60])
    finally:
        session.close()


if __name__ == "__main__":
    main()
