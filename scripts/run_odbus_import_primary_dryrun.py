"""Backward-compatible wrapper — delegates to run_odbus_import.py --dry-run."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_odbus_import import main

if __name__ == "__main__":
    if "--dry-run" not in sys.argv and "--apply" not in sys.argv:
        sys.argv.insert(1, "--dry-run")
    if "--use-production" not in sys.argv:
        sys.argv.insert(1, "--use-production")
    main()
