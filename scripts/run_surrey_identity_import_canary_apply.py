#!/usr/bin/env python3
"""Fixed Class-C Surrey identity-aware import canary apply (PR-EN1F-3).

Requires a fresh Class-A dry-run artifact from
scripts/run_surrey_identity_import_canary.py at the exact current git SHA,
with all three risk counters (duplicate_risk, invalid_rows,
duplicate_source_rows) at zero and at least 20 planned updates plus 5
planned inserts. Before any write, re-fetches live Surrey source data and
rebuilds the full plan; the full row count and plan_digest must match the
artifact exactly -- any drift anywhere in the full candidate universe,
not just the applied slice, fails closed before a single row is touched.

The canary itself is fixed and deterministic: exactly 20 update
candidates and exactly 5 insert candidates, selected by sorting each
outcome group by its own canonical key -- the full official
PermitNumber, in lexicographic order -- independent of the order the
ArcGIS source (or any other caller) happens to return rows in. There is
no --limit, --force, sample-size, or arbitrary-id flag.

Applied via db.surrey_permit_import.upsert_surrey_permits_identity_aware
in one caller-owned transaction: commits only if the writer reports
exactly 20 updates and 5 inserts, full rollback on any exception,
conflict, or rowcount/partial-result mismatch. The post-success artifact
contains only aggregates and a canary digest -- never raw ids,
PermitNumbers, or applicant text.

Production use requires --allow-production and this repository's
real-TTY human confirmation phrase.
"""

from __future__ import annotations

import argparse
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
    FIXED_INSERT_LIMIT,
    FIXED_UPDATE_LIMIT,
    apply_surrey_identity_import_canary,
)
from scripts.run_surrey_identity_import_canary import (  # noqa: E402
    TRANSACTION_MODE,
    fetch_official_source_rows,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = (
    ROOT / "exports" / "surrey_identity_import_canary_production_class_a.json"
)
DEFAULT_OUTPUT_PATH = (
    ROOT / "exports" / "surrey_identity_import_canary_apply_result.json"
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class SurreyImportCanaryApplyError(ValueError):
    """Raised when the reviewed artifact or apply result is unsafe."""


def load_and_validate_artifact(
    artifact_path: Path,
    *,
    current_git_sha: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurreyImportCanaryApplyError(
            "canary artifact is missing, unreadable, or invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SurreyImportCanaryApplyError("canary artifact must be a JSON object")
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise SurreyImportCanaryApplyError("canary artifact schema version mismatch")
    if payload.get("git_commit_sha") != current_git_sha:
        raise SurreyImportCanaryApplyError("canary artifact git SHA mismatch")
    if payload.get("source") != "surrey":
        raise SurreyImportCanaryApplyError("canary artifact source mismatch")
    if payload.get("transaction_mode") != TRANSACTION_MODE:
        raise SurreyImportCanaryApplyError("canary artifact was not built read-only")

    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise SurreyImportCanaryApplyError("canary artifact counts are missing")

    integer_fields = {
        "invalid_rows": counts.get("invalid_rows"),
        "duplicate_source_rows": counts.get("duplicate_source_rows"),
        "duplicate_risk": counts.get("duplicate_risk"),
        "planned_updates": counts.get("planned_updates"),
        "planned_inserts": counts.get("planned_inserts"),
    }
    for name, value in integer_fields.items():
        if type(value) is not int or value < 0:
            raise SurreyImportCanaryApplyError(
                f"canary artifact has invalid integer field: {name}"
            )

    for field in ("invalid_rows", "duplicate_source_rows", "duplicate_risk"):
        if counts[field] != 0:
            raise SurreyImportCanaryApplyError(
                f"canary artifact contains unsafe condition: {field}"
            )

    if counts["planned_updates"] < FIXED_UPDATE_LIMIT:
        raise SurreyImportCanaryApplyError(
            f"canary artifact has fewer than {FIXED_UPDATE_LIMIT} planned updates"
        )
    if counts["planned_inserts"] < FIXED_INSERT_LIMIT:
        raise SurreyImportCanaryApplyError(
            f"canary artifact has fewer than {FIXED_INSERT_LIMIT} planned inserts"
        )

    if not _DIGEST_RE.fullmatch(str(payload.get("plan_digest") or "")):
        raise SurreyImportCanaryApplyError(
            "canary artifact has invalid digest field: plan_digest"
        )
    return payload


def execute_import_canary(session, *, source_rows, artifact) -> dict[str, Any]:
    """Apply and commit the fixed 20/5 canary batch; roll back on every
    failure, including a partial or oversized result."""
    try:
        result = apply_surrey_identity_import_canary(
            session,
            rows=source_rows,
            expected_plan_digest=artifact["plan_digest"],
        )
        if result.get("updated") != FIXED_UPDATE_LIMIT:
            raise SurreyImportCanaryApplyError(
                f"writer did not update exactly {FIXED_UPDATE_LIMIT} rows "
                f"(got {result.get('updated')!r})"
            )
        if result.get("inserted") != FIXED_INSERT_LIMIT:
            raise SurreyImportCanaryApplyError(
                f"writer did not insert exactly {FIXED_INSERT_LIMIT} rows "
                f"(got {result.get('inserted')!r})"
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
        operation="Surrey identity-aware import canary (fixed 20 updates + 5 inserts)",
        nominal_class=SafetyClass.C,
    )

    source_rows = fetch_official_source_rows()
    session = get_session()
    try:
        result = execute_import_canary(
            session,
            source_rows=source_rows,
            artifact=artifact,
        )
    finally:
        session.close()

    output_artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": current_git_sha,
        "source": "surrey",
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "updated": result["updated"],
        "inserted": result["inserted"],
        "plan_digest": result["plan_digest"],
        "canary_digest": result["canary_digest"],
    }
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(output_artifact, indent=2), encoding="utf-8")
    print(json.dumps(output_artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
