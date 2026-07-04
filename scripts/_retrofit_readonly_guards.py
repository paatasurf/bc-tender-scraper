#!/usr/bin/env python3
"""Add guard_readonly_db to probe/audit scripts missing db_safety."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

ALREADY = {
    "demo_db_safety_guard.py",
    "_retrofit_db_guards.py",
    "run_company_canonical_merge.py",
    "f005_purge_non_construction_matches.py",
    "apply_permit_migration.py",
    "backfill_closing_at_local.py",
    "backfill_identity_phase2.py",
    "run_orgbook_import.py",
    "run_odbus_import.py",
    "run_construction_tiers.py",
    "run_registry_verification_match.py",
    "run_vancouver_permit_backfill.py",
    "migrate_architects_to_arch_companies.py",
}


def retrofit(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "db.db_safety" in text or path.name in ALREADY:
        return False
    if "db.connection" not in text and "get_engine" not in text:
        return False

    if "from pathlib import Path" not in text:
        if "from __future__" in text:
            text = re.sub(
                r"(from __future__ import annotations\n+)",
                r"\1\nfrom pathlib import Path\n",
                text,
                count=1,
            )
        else:
            text = "from pathlib import Path\n\n" + text

    if "sys.path.insert" not in text:
        block = (
            "import sys\n\n"
            "ROOT = Path(__file__).resolve().parents[1]\n"
            "if str(ROOT) not in sys.path:\n"
            "    sys.path.insert(0, str(ROOT))\n\n"
        )
        if "import sys" not in text:
            text = re.sub(r"(from pathlib import Path\n)", r"\1\n" + block, text, count=1)

    guard_import = "from db.db_safety import guard_readonly_db\n_SCRIPT = Path(__file__).name\n"
    if "from db.connection import" in text:
        text = re.sub(
            r"(from db\.connection import[^\n]+\n)",
            r"\1" + guard_import,
            text,
            count=1,
        )
    elif "import config.env" in text:
        text = re.sub(
            r"(import config\.env[^\n]*\n)",
            r"\1\n" + guard_import,
            text,
            count=1,
        )
    else:
        return False

    text = re.sub(r"\n\s*init_db\(\)\n", "\n", text)
    text = re.sub(r"\n\s*init_db\(raise_on_failure=True\)\n", "\n", text)

    if "def main" in text and "guard_readonly_db(_SCRIPT)" not in text:
        text = re.sub(
            r"(def main\([^)]*\)[^:]*:\n)",
            r"\1    guard_readonly_db(_SCRIPT)\n",
            text,
            count=1,
        )
    elif "guard_readonly_db(_SCRIPT)" not in text and "get_engine()" in text:
        text = re.sub(
            r"(import config\.env[^\n]*\n)",
            r"\1guard_readonly_db(_SCRIPT)\n",
            text,
            count=1,
        )

    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    updated = [p.name for p in sorted(SCRIPTS.glob("*.py")) if retrofit(p)]
    print(f"Updated {len(updated)} scripts")
    for name in updated:
        print(" ", name)


if __name__ == "__main__":
    main()
