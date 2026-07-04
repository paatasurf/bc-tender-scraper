"""Generate scripts/CLASSIFICATION.md from script inventory heuristics + overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
OUTPUT = SCRIPTS_DIR / "CLASSIFICATION.md"
LAST_REVIEWED = "2026-07-03"

HEADER = """# Script classification registry

Single source of truth for CLI script database risk classes.

## Classes

| Class | Name | Writes DB | Calls init_db | Production |
|-------|------|-----------|---------------|------------|
| **A** | No Write | No (read-only SELECT or no DB) | No | Read-only via `--use-production` when script reads DB |
| **B** | Local Write | Yes (data) | No | Local default; `--allow-production` + phrase for prod |
| **C** | Registry Write | Yes (registry data) | Sometimes | Local default; `--allow-production` + valid dry-run artifact |
| **D** | Schema DDL | Yes (schema/data) | Yes | `--allow-production` + confirmation phrase only |

**A vs B:** Class A never mutates the database (includes scripts with no Postgres
connection and read-only probes). Class B performs local data writes (backfill,
cache warm, staging loads) but not registry merge workflows.

## Runtime escalation (critical)

The **highest-risk operation actually executed at runtime** determines the
**effective** class, regardless of nominal class.

If a Class A, B, or C script calls `init_db()` or any DDL mid-run, `db_safety.py`
escalates to **Class D** at that point and re-checks authorization (including
production confirmation when applicable).

## Class C dry-run validity

`run_company_canonical_merge.py --apply` refuses when the referenced dry-run
artifact lacks matching `git_commit_sha` and `dataset_fingerprint` (counts,
max timestamps, identity checksum, schema migration version).

---

| Script | Nominal Class | Writes DB | Calls init_db | Production Allowed | Last Reviewed | Notes |
|--------|---------------|-----------|---------------|-------------------|---------------|-------|
"""

OVERRIDES: dict[str, dict[str, str]] = {
    "run_company_canonical_merge.py": {
        "nominal": "C",
        "notes": "Effective escalates to Class D whenever init_db() runs. --apply requires fresh dry-run artifact.",
    },
    "demo_class_b_escalation.py": {
        "nominal": "B",
        "notes": "Demo: nominal B (local write), escalates to D on init_db().",
    },
    "f005_purge_non_construction_matches.py": {
        "nominal": "B",
        "notes": "Dry-run default (Class A path); --apply deletes rows (Class B write).",
    },
    "migrate_architects_to_arch_companies.py": {
        "nominal": "B",
        "notes": "Dry-run default; --commit inserts rows.",
    },
    "warm_tender_match_cache.py": {
        "nominal": "B",
        "notes": "Writes tender_matches cache rows.",
    },
    "_insert_test_client_profile.py": {
        "nominal": "B",
        "notes": "Upserts client_profiles test row.",
    },
    "build_enterprise_registry_seed.py": {
        "nominal": "C",
        "notes": "Effective D when init_db runs; --skip-init-db keeps read path at A.",
    },
    "google_enrichment_rating_gap_audit.py": {
        "nominal": "A",
        "notes": "Read-only DB + external Apify; never writes.",
    },
    "investigate_closing_dates.py": {
        "nominal": "A",
        "notes": "Read-only DB investigation.",
    },
    "demo_db_safety_guard.py": {
        "nominal": "A",
        "notes": "Spawns subprocess only; no direct DB.",
    },
    "_retrofit_db_guards.py": {"nominal": "A", "notes": "Codegen utility."},
    "_retrofit_readonly_guards.py": {"nominal": "A", "notes": "Codegen utility."},
    "generate_classification_md.py": {"nominal": "A", "notes": "Codegen utility."},
    "_smoke_google_enrichment_8638.py": {
        "nominal": "A",
        "notes": "HTTP smoke against Railway API; no direct Postgres.",
    },
    "_audit_person_permit_pipeline.py": {
        "nominal": "A",
        "notes": "Read-only audit pipeline.",
    },
    "_probe_alias_breakdown.py": {"nominal": "A", "notes": "Read-only probe."},
    "_probe_ledcor_faucet.py": {"nominal": "A", "notes": "Read-only probe."},
    "_probe_db_enterprise_seed.py": {"nominal": "A", "notes": "Read-only probe."},
    "_verify_merge_apply_local.py": {"nominal": "A", "notes": "Post-apply verification reads only."},
}

REGISTRY_C_SCRIPTS = {
    "run_company_canonical_merge.py",
    "run_orgbook_import.py",
    "run_odbus_import.py",
    "run_registry_verification_match.py",
    "build_enterprise_registry_seed.py",
}

LOCAL_WRITE_B_SCRIPTS = {
    "f005_purge_non_construction_matches.py",
    "migrate_architects_to_arch_companies.py",
    "warm_tender_match_cache.py",
    "_insert_test_client_profile.py",
    "backfill_closing_at_local.py",
    "backfill_identity_phase2.py",
    "run_vancouver_permit_backfill.py",
}


@dataclass
class Row:
    script: str
    nominal: str
    writes_db: str
    init_db: str
    production: str
    last_reviewed: str
    notes: str


def _classify(path: Path) -> Row:
    name = path.name
    text = path.read_text(encoding="utf-8", errors="replace")
    override = OVERRIDES.get(name, {})

    touches_db = bool(
        re.search(r"db\.connection|get_session|get_engine|init_db|create_engine|session_scope", text)
    )
    calls_init = bool(re.search(r"\binit_db\s*\(", text))
    writes = bool(
        re.search(
            r"\.commit\(|\.delete\(|apply_merge_plan|warm_hybrid|backfill_|import_orgbook|import_odbus",
            text,
            re.I,
        )
    ) or bool(re.search(r'add_argument\(\s*"--apply', text))
    readonly_guard = "guard_readonly" in text
    registry_c = name in REGISTRY_C_SCRIPTS or "SafetyClass.C" in text
    local_b = name in LOCAL_WRITE_B_SCRIPTS

    if override.get("nominal"):
        nominal = override["nominal"]
    elif name.startswith("_probe_") or name.startswith("_readonly_"):
        nominal = "A"
    elif not touches_db:
        nominal = "A"
    elif calls_init or "ALTER TABLE" in text or "apply_permit_migration" in name:
        nominal = "D"
    elif registry_c:
        nominal = "C"
    elif local_b or (writes and not calls_init):
        nominal = "B"
    elif readonly_guard or (touches_db and not writes):
        nominal = "A"
    else:
        nominal = "A"

    if nominal == "A":
        prod = "Read-only (`--use-production`)" if touches_db else "N/A"
        writes_db = "No"
    elif nominal == "B":
        prod = "Local write; `--allow-production` + phrase"
        writes_db = "Yes"
    elif nominal == "C":
        prod = "Local write; `--allow-production` + dry-run"
        writes_db = "Yes"
    else:
        prod = "`--allow-production` + phrase"
        writes_db = "Yes"

    notes = override.get("notes", "")
    if not notes and name.startswith("_probe_"):
        notes = "Read-only probe."
        if calls_init:
            notes += " Calls init_db() — effective Class D at runtime."
    if not notes and name.startswith("_readonly_"):
        notes = "Read-only audit."
    if not notes and name.startswith("_research_"):
        notes = "External research; no DB."
    if not notes and (name.startswith("verify_") or name.startswith("validate_")):
        notes = "Verification script."

    return Row(
        script=name,
        nominal=nominal,
        writes_db=writes_db,
        init_db="Yes" if calls_init else "No",
        production=prod,
        last_reviewed=LAST_REVIEWED,
        notes=notes,
    )


def main() -> None:
    rows: list[Row] = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        if path.name == "generate_classification_md.py":
            continue
        rows.append(_classify(path))

    lines = [HEADER]
    for row in rows:
        notes = row.notes.replace("|", "\\|")
        lines.append(
            f"| `{row.script}` | {row.nominal} | {row.writes_db} | {row.init_db} | {row.production} | {row.last_reviewed} | {notes} |"
        )
    lines.append("")
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT} ({len(rows)} scripts)")


if __name__ == "__main__":
    main()
