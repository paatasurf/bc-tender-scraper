#!/usr/bin/env python3
"""One-shot retrofit: add db_safety guards to scripts that lack them."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

DESTRUCTIVE = {
    "run_orgbook_import.py",
    "run_odbus_import.py",
    "run_construction_tiers.py",
    "run_registry_verification_match.py",
    "run_vancouver_permit_backfill.py",
    "backfill_identity_phase2.py",
    "migrate_architects_to_arch_companies.py",
    "build_enterprise_registry_seed.py",
    "_insert_test_client_profile.py",
    "smoke_bd_intelligence.py",
    "warm_tender_match_cache.py",
    "populate_project_contacts.py",
    "enrich_early_signal_events.py",
}

SKIP = {
    "demo_db_safety_guard.py",
    "_retrofit_db_guards.py",
    "run_company_canonical_merge.py",
    "f005_purge_non_construction_matches.py",
    "apply_permit_migration.py",
    "backfill_closing_at_local.py",
}


def _has_guard(text: str) -> bool:
    return "db.db_safety" in text or "db_safety" in text


def _ensure_path_block(text: str) -> str:
    if "sys.path.insert" in text and "parents[1]" in text:
        return text
    header = (
        "import sys\nfrom pathlib import Path\n\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "if str(ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(ROOT))\n\n"
    )
    if "from __future__" in text:
        text = re.sub(
            r"(from __future__ import annotations\n+)",
            r"\1\n" + header,
            text,
            count=1,
        )
    else:
        text = header + text
    return text


def _add_imports(text: str, *, readonly: bool) -> str:
    if readonly:
        imp = "from db.db_safety import add_production_safety_args, guard_readonly_db\n"
        script_line = "_SCRIPT = Path(__file__).name\n"
    else:
        imp = "from db.db_safety import add_production_safety_args, guard_destructive_db_from_args\n"
        script_line = "_SCRIPT = Path(__file__).name\n"

    if imp.strip() in text:
        return text
    # After db.connection import if present
    if "from db.connection import" in text:
        text = re.sub(
            r"(from db\.connection import[^\n]+\n)",
            r"\1" + imp + script_line,
            text,
            count=1,
        )
    elif "import config.env" in text:
        text = re.sub(
            r"(import config\.env[^\n]*\n)",
            r"\1\n" + imp + script_line,
            text,
            count=1,
        )
    else:
        text = imp + script_line + text
    return text


def _inject_readonly_guard(text: str) -> str:
    if "guard_readonly_db" in text:
        return text
    # Module-level get_engine
    if re.search(r"^from db\.connection import get_engine", text, re.M) and "def main" not in text:
        text = re.sub(
            r"(import config\.env[^\n]*\n)",
            r"\1\nfrom db.db_safety import guard_readonly_db\n_SCRIPT = Path(__file__).name\n"
            r"guard_readonly_db(_SCRIPT)\n",
            text,
            count=1,
        )
        return text
    # Before init_db() or get_session() or session_scope or get_engine in main
    if "init_db()" in text:
        text = text.replace("init_db()", "guard_readonly_db(_SCRIPT)\n    # init_db skipped — read-only probe")
        text = re.sub(r"\n\s*# init_db skipped — read-only probe\n", "\n    guard_readonly_db(_SCRIPT)\n", text)
    elif "def main" in text:
        text = re.sub(
            r"(def main\([^)]*\)[^:]*:\n)",
            r"\1    guard_readonly_db(_SCRIPT)\n",
            text,
            count=1,
        )
    return text


def _inject_destructive_guard(text: str) -> str:
    if "guard_destructive_db" in text:
        return text
    if "add_production_safety_args" not in text:
        text = re.sub(
            r"(parser = argparse\.ArgumentParser[^\n]*\n)",
            r"\1    add_production_safety_args(parser)\n",
            text,
            count=1,
        )
    text = re.sub(
        r"(args = parser\.parse_args\(\)\n)",
        r"\1    guard_destructive_db_from_args(args, script_name=_SCRIPT, operation=\"database write\")\n",
        text,
        count=1,
    )
    return text


def retrofit_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if _has_guard(text):
        return False
    if "db.connection" not in text and "get_engine" not in text:
        return False

    readonly = path.name not in DESTRUCTIVE
    text = _ensure_path_block(text)
    text = _add_imports(text, readonly=readonly)
    if readonly:
        text = _inject_readonly_guard(text)
    else:
        if "argparse" not in text:
            return False
        text = _inject_destructive_guard(text)
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    updated = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name in SKIP:
            continue
        if retrofit_file(path):
            updated.append(path.name)
    print("Updated:", len(updated))
    for name in updated:
        print(" ", name)


if __name__ == "__main__":
    main()
