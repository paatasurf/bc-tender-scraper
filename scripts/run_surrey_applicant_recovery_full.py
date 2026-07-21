#!/usr/bin/env python3
"""Apply the full reviewed Surrey applicant recovery batch (Class C).

Requires a fresh schema-v2 artifact from the Class-A dry-run at the exact
current git SHA, with every safety counter at zero. Production use
additionally requires --allow-production and the repository's real-TTY
human confirmation phrase. There is no configurable batch-size, limit, or
force flag: every eligible row from the artifact is applied, or none are
-- a single atomic transaction, rolled back on any exception or partial
result.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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
from pipeline.surrey_applicant_recovery import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    apply_surrey_applicant_recovery,
)
from scripts.run_surrey_applicant_recovery_dryrun import (  # noqa: E402
    TRANSACTION_MODE,
    fetch_official_source_rows,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = (
    ROOT / "exports" / "surrey_applicant_recovery_dryrun_production_class_a_v2.json"
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

SAFETY_COUNTER_FIELDS = (
    "invalid_source_ids",
    "duplicate_source_ids",
    "ambiguous_legacy_prefixes",
    "duplicate_legacy_prefix_rows",
    "ambiguous_production_external_ids",
)


class SurreyApplicantFullRecoveryError(ValueError):
    """Raised when the reviewed artifact or full-recovery result is unsafe."""


def load_and_validate_artifact(
    artifact_path: Path,
    *,
    current_git_sha: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact is missing, unreadable, or invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact must be a JSON object"
        )
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact schema version mismatch"
        )
    if payload.get("git_commit_sha") != current_git_sha:
        raise SurreyApplicantFullRecoveryError("recovery artifact git SHA mismatch")
    if payload.get("source") != "surrey":
        raise SurreyApplicantFullRecoveryError("recovery artifact source mismatch")
    if payload.get("transaction_mode") != TRANSACTION_MODE:
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact was not built read-only"
        )

    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise SurreyApplicantFullRecoveryError("recovery artifact counts are missing")

    candidate_count = payload.get("candidate_count")
    integer_fields = {
        "candidate_count": candidate_count,
        "recoverable_blank_applicant": counts.get("recoverable_blank_applicant"),
        **{name: counts.get(name) for name in SAFETY_COUNTER_FIELDS},
    }
    for name, value in integer_fields.items():
        if type(value) is not int or value < 0:
            raise SurreyApplicantFullRecoveryError(
                f"recovery artifact has invalid integer field: {name}"
            )

    if counts["recoverable_blank_applicant"] != candidate_count:
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact candidate count invariant failed"
        )
    for field in SAFETY_COUNTER_FIELDS:
        if counts[field] != 0:
            raise SurreyApplicantFullRecoveryError(
                f"recovery artifact contains unsafe source/key condition: {field}"
            )

    if candidate_count <= 0:
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact is stale: candidate_count is 0 -- nothing left to "
            "recover, or the artifact predates a prior successful recovery run"
        )

    if not _DIGEST_RE.fullmatch(str(payload.get("candidate_set_digest") or "")):
        raise SurreyApplicantFullRecoveryError(
            "recovery artifact has invalid digest field: candidate_set_digest"
        )
    return payload


def execute_full_recovery(session, *, source_rows, artifact) -> dict[str, Any]:
    """Apply and commit the full reviewed batch; roll back on every failure."""
    expected_count = artifact["candidate_count"]
    try:
        result = apply_surrey_applicant_recovery(
            session,
            source_rows=source_rows,
            candidate_limit=expected_count,
            expected_candidate_set_digest=artifact["candidate_set_digest"],
        )
        if result.get("eligible_count") != expected_count:
            raise SurreyApplicantFullRecoveryError(
                "live eligible candidate count no longer matches the reviewed artifact"
            )
        if result.get("selected_count") != expected_count:
            raise SurreyApplicantFullRecoveryError(
                "writer did not select exactly the reviewed candidate_count rows"
            )
        if result.get("updated_count") != expected_count:
            raise SurreyApplicantFullRecoveryError(
                "writer did not update exactly the reviewed candidate_count rows"
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
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=DEFAULT_ARTIFACT_PATH,
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error(
            "--apply is required; generate the artifact with the Class-A dry-run script"
        )

    artifact = load_and_validate_artifact(
        args.artifact_path,
        current_git_sha=get_git_commit_sha(),
    )
    guard_destructive_db_from_args(
        args,
        script_name=_SCRIPT,
        operation="Surrey blank-applicant full recovery (full reviewed batch)",
        nominal_class=SafetyClass.C,
    )

    source_rows = fetch_official_source_rows()
    session = get_session()
    try:
        result = execute_full_recovery(
            session,
            source_rows=source_rows,
            artifact=artifact,
        )
    finally:
        session.close()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
