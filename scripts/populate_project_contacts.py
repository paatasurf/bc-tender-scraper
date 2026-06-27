"""Populate project_contacts from permits and early_signal_events."""

from __future__ import annotations

import json
import sys

from db.connection import get_session, init_db
from pipeline.project_intelligence import rebuild_project_contacts


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_db()
    session = get_session()
    try:
        result = rebuild_project_contacts(session)
    finally:
        session.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
