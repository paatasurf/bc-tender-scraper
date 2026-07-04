#!/usr/bin/env python3
"""Demonstrate db_safety guard refusing a simulated production URL.

Does NOT connect to any database. Safe to run anywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIMULATED_PROD = "postgresql://guard_test:guard_test@acela.proxy.rlwy.net:47306/railway"


def _run_merge_apply() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = SIMULATED_PROD
    env.pop("DATABASE_URL_PRODUCTION", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_company_canonical_merge.py"), "--apply"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def main() -> int:
    print("=== db_safety guard demo (simulated production URL) ===")
    print(f"DATABASE_URL={SIMULATED_PROD}")
    print("")
    result = _run_merge_apply()
    print("--- stdout ---")
    print(result.stdout)
    print("--- stderr ---")
    print(result.stderr)
    print(f"--- exit code: {result.returncode} ---")

    refused = result.returncode != 0 and (
        "[db_safety] REFUSED" in result.stderr
        or "[db_safety] Refusing" in result.stderr
    )
    if refused:
        print("\nPASS: guard refused destructive run against simulated production.")
        return 0
    print(
        "\nFAIL: expected refusal with [db_safety] REFUSED or Refusing in stderr.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
