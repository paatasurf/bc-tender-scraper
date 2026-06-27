"""Populate project_contacts from permits and early_signal_events."""

from __future__ import annotations

import json
import sys

from pipeline.internal_steps import run_populate_project_contacts_step


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result = run_populate_project_contacts_step()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
