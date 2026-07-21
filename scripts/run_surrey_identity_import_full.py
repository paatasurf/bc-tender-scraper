#!/usr/bin/env python3
"""Full Class-C Surrey identity-aware import apply (PR-EN1F-5).

Requires a fresh Class-A dry-run artifact from
scripts/run_surrey_identity_import_canary.py at the exact current git SHA,
with all three risk counters (duplicate_risk, invalid_rows,
duplicate_source_rows) at zero and at least one planned update or insert.
Before any write, re-fetches live Surrey source data and rebuilds the
full plan; every count in the artifact and the full plan_digest must
match exactly -- any drift anywhere in the full candidate universe fails
closed before a single row is touched.

Applies the ENTIRE reviewed plan -- however many updates and inserts the
freshly rebuilt plan actually contains, never a hardcoded number -- via
db.surrey_permit_import.upsert_surrey_permits_identity_aware in one
caller-owned transaction: commits only if the writer's applied counts
exactly match the freshly reviewed plan's counts, full rollback on any
exception, conflict, or count mismatch. There is no --limit, --force,
sample-size, or arbitrary-id flag.

Production use requires --allow-production and this repository's
real-TTY human confirmation phrase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: E402,F401

from db.classification import SafetyClass  # noqa: E402
from db.connection import get_session  # noqa: E402
from db.db_safety import (  # noqa: E402
    add_production_safety_args,
    guard_destructive_db_from_args,
)
from db.merge_dry_run_provenance import get_git_commit_sha  # noqa: E402
from pipeline.surrey_identity_import_canary import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    apply_surrey_identity_import_full,
)
from scripts.run_surrey_identity_import_canary import (  # noqa: E402
    TRANSACTION_MODE,
    fetch_official_source_rows,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = (
    ROOT / "exports" / "surrey_identity_import_canary_production_class_a.json"
)
DEFAULT_OUTPUT_PATH = ROOT / "exports" / "surrey_identity_import_full_result.json"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_COUNT_FIELDS = (
    "source_total",
    "invalid_rows",
    "duplicate_source_rows",
    "production_total",
    "planned_updates",
    "planned_inserts",
    "duplicate_risk",
)


class SurreyImportFullApplyError(ValueError):
    """Raised when the reviewed artifact or apply result is unsafe."""


def load_and_validate_artifact(
    artifact_path: Path,
    *,
    current_git_sha: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurreyImportFullApplyError(
            "import artifact is missing, unreadable, or invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SurreyImportFullApplyError("import artifact must be a JSON object")
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise SurreyImportFullApplyError("import artifact schema version mismatch")
    if payload.get("git_commit_sha") != current_git_sha:
        raise SurreyImportFullApplyError("import artifact git SHA mismatch")
    if payload.get("source") != "surrey":
        raise SurreyImportFullApplyError("import artifact source mismatch")
    if payload.get("transaction_mode") != TRANSACTION_MODE:
        raise SurreyImportFullApplyError("import artifact was not built read-only")

    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise SurreyImportFullApplyError("import artifact counts are missing")

    for name in _COUNT_FIELDS:
        value = counts.get(name)
        if type(value) is not int or value < 0:
            raise SurreyImportFullApplyError(
                f"import artifact has invalid integer field: {name}"
            )

    for field in ("invalid_rows", "duplicate_source_rows", "duplicate_risk"):
        if counts[field] != 0:
            raise SurreyImportFullApplyError(
                f"import artifact contains unsafe condition: {field}"
            )

    if counts["planned_updates"] <= 0 and counts["planned_inserts"] <= 0:
        raise SurreyImportFullApplyError(
            "import artifact is stale: no planned updates or inserts -- "
            "nothing left to import, or the artifact predates a prior "
            "successful apply run"
        )

    if not _DIGEST_RE.fullmatch(str(payload.get("plan_digest") or "")):
        raise SurreyImportFullApplyError(
            "import artifact has invalid digest field: plan_digest"
        )
    return payload


def compute_result_digest(*, plan_digest: str, updated: int, inserted: int) -> str:
    """A compact, tamper-evident receipt tying the reviewed plan_digest to
    the actual applied counts. Deliberately distinct from plan_digest --
    it changes if either the reviewed plan or the applied outcome
    changes, and is safe to publish (no raw ids/PermitNumbers/text)."""
    return hashlib.sha256(
        f"{plan_digest}:{updated}:{inserted}".encode("utf-8")
    ).hexdigest()


def execute_full_import(session, *, source_rows, artifact) -> dict[str, Any]:
    """Apply and commit the full reviewed import batch; roll back on
    every failure, including a partial or drifted result."""
    expected_updates = artifact["counts"]["planned_updates"]
    expected_inserts = artifact["counts"]["planned_inserts"]
    try:
        result = apply_surrey_identity_import_full(
            session,
            rows=source_rows,
            expected_plan_digest=artifact["plan_digest"],
        )
        if result.get("eligible_updates") != expected_updates:
            raise SurreyImportFullApplyError(
                "live eligible update count no longer matches the reviewed artifact"
            )
        if result.get("eligible_inserts") != expected_inserts:
            raise SurreyImportFullApplyError(
                "live eligible insert count no longer matches the reviewed artifact"
            )
        if result.get("updated") != expected_updates:
            raise SurreyImportFullApplyError(
                "writer did not update exactly the reviewed planned_updates rows"
            )
        if result.get("inserted") != expected_inserts:
            raise SurreyImportFullApplyError(
                "writer did not insert exactly the reviewed planned_inserts rows"
            )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_production_safety_args(parser)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--artifact-path", type=Path, default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    if not args.apply:
        parser.error(
            "--apply is required; generate the artifact with the Class-A dry-run script"
        )

    current_git_sha = get_git_commit_sha()
    artifact = load_and_validate_artifact(
        args.artifact_path,
        current_git_sha=current_git_sha,
    )
    guard_destructive_db_from_args(
        args,
        script_name=_SCRIPT,
        operation="Surrey identity-aware import full apply",
        nominal_class=SafetyClass.C,
    )

    source_rows = fetch_official_source_rows()
    session = get_session()
    try:
        result = execute_full_import(
            session,
            source_rows=source_rows,
            artifact=artifact,
        )
    finally:
        session.close()

    result_digest = compute_result_digest(
        plan_digest=result["plan_digest"],
        updated=result["updated"],
        inserted=result["inserted"],
    )
    output_artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": current_git_sha,
        "source": "surrey",
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "updated": result["updated"],
        "inserted": result["inserted"],
        "plan_digest": result["plan_digest"],
        "result_digest": result_digest,
    }
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(output_artifact, indent=2), encoding="utf-8")
    print(json.dumps(output_artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
