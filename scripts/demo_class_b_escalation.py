#!/usr/bin/env python3
"""Demo: nominal Class B script escalates to Class D when init_db() runs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unittest.mock import patch

from db.classification import SafetyClass
from db.connection import init_db
from db.db_safety import begin_script_guard, effective_class

_SCRIPT = Path(__file__).name


def main() -> int:
    print(f"[demo] Starting with nominal {SafetyClass.B.label}")
    begin_script_guard(SafetyClass.B, _SCRIPT)
    print(f"[demo] Effective class before init_db: {effective_class().label if effective_class() else 'none'}")
    print("[demo] Simulating read-only phase complete; calling init_db()...")
    with patch("db.connection._run_migrations"):
        init_db(raise_on_failure=False)
    print(f"[demo] Effective class after init_db: {effective_class().label if effective_class() else 'none'}")
    print("[demo] Class D authorization succeeded — would proceed with DDL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
