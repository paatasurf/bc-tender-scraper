#!/usr/bin/env python3
"""Apply exactly 25 reviewed Surrey applicant recoveries (Class C).

Requires a fresh schema-v2 artifact from the Class-A dry-run at the exact
current git SHA. Production use additionally requires --allow-production and
the repository's real-TTY human confirmation phrase. There is no configurable
batch-size flag: this first canary is source-code bounded to 25 rows.
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
    RECOMMENDED_CANARY_LIMIT,
    apply_surrey_applicant_recovery,
)
from scripts.run_surrey_applicant_recovery_dryrun import (  # noqa: E402
    TRANSACTION_MODE,
    fetch_official_source_rows,
)

_SCRIPT = Path(__file__).name
CANARY_LIMIT = 25
DEFAULT_ARTIFACT_PATH = (
    ROOT / "exports" / "surrey_applicant_recovery_dryrun_production_class_a_v2.json"
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class SurreyApplicantCanaryError(ValueError):
    """Raised when the reviewed artifact or canary result is unsafe."""


def load_and_validate_artifact(
    artifact_path: Path,
    *,
    current_git_sha: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurreyApplicantCanaryError(
            "canary artifact is missing, unreadable, or invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SurreyApplicantCanaryError("canary artifact must be a JSON object")
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise SurreyApplicantCanaryError("canary artifact schema version mismatch")
    if payload.get("git_commit_sha") != current_git_sha:
        raise SurreyApplicantCanaryError("canary artifact git SHA mismatch")
    if payload.get("source") != "surrey":
        raise SurreyApplicantCanaryError("canary artifact source mismatch")
    if payload.get("transaction_mode") != TRANSACTION_MODE:
        raise SurreyApplicantCanaryError("canary artifact was not built read-only")

    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise SurreyApplicantCanaryError("canary artifact counts are missing")
    integer_fields = {
        "candidate_count": payload.get("candidate_count"),
        "recommended_canary_limit": payload.get("recommended_canary_limit"),
        "canary_candidate_count": payload.get("canary_candidate_count"),
        "recoverable_blank_applicant": counts.get("recoverable_blank_applicant"),
        "invalid_source_ids": counts.get("invalid_source_ids"),
        "duplicate_source_ids": counts.get("duplicate_source_ids"),
        "ambiguous_legacy_prefixes": counts.get("ambiguous_legacy_prefixes"),
        "duplicate_legacy_prefix_rows": counts.get("duplicate_legacy_prefix_rows"),
        "ambiguous_production_external_ids": counts.get(
            "ambiguous_production_external_ids"
        ),
    }
    for name, value in integer_fields.items():
        if type(value) is not int or value < 0:
            raise SurreyApplicantCanaryError(
                f"canary artifact has invalid integer field: {name}"
            )

    if RECOMMENDED_CANARY_LIMIT != CANARY_LIMIT:
        raise SurreyApplicantCanaryError("code-level canary limits disagree")
    if payload["recommended_canary_limit"] != CANARY_LIMIT:
        raise SurreyApplicantCanaryError("artifact canary limit mismatch")
    if payload["canary_candidate_count"] != CANARY_LIMIT:
        raise SurreyApplicantCanaryError("artifact is not a full 25-row canary")
    if payload["candidate_count"] < CANARY_LIMIT:
        raise SurreyApplicantCanaryError("artifact has fewer than 25 candidates")
    if counts["recoverable_blank_applicant"] != payload["candidate_count"]:
        raise SurreyApplicantCanaryError("artifact candidate count invariant failed")
    for field in (
        "invalid_source_ids",
        "duplicate_source_ids",
        "ambiguous_legacy_prefixes",
        "duplicate_legacy_prefix_rows",
        "ambiguous_production_external_ids",
    ):
        if counts[field] != 0:
            raise SurreyApplicantCanaryError(
                f"artifact contains unsafe source/key condition: {field}"
            )

    for field in ("candidate_set_digest", "canary_candidate_set_digest"):
        if not _DIGEST_RE.fullmatch(str(payload.get(field) or "")):
            raise SurreyApplicantCanaryError(
                f"artifact has invalid digest field: {field}"
            )
    return payload


def execute_canary(session, *, source_rows, artifact) -> dict[str, Any]:
    """Apply and commit one atomic canary; roll back on every failure."""
    try:
        result = apply_surrey_applicant_recovery(
            session,
            source_rows=source_rows,
            candidate_limit=CANARY_LIMIT,
            expected_candidate_set_digest=artifact["canary_candidate_set_digest"],
        )
        if result.get("selected_count") != CANARY_LIMIT:
            raise SurreyApplicantCanaryError(
                "writer did not select exactly 25 canary rows"
            )
        if result.get("updated_count") != CANARY_LIMIT:
            raise SurreyApplicantCanaryError(
                "writer did not update exactly 25 canary rows"
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
            "--apply is required; generate the artifact with the Class-A script"
        )

    artifact = load_and_validate_artifact(
        args.artifact_path,
        current_git_sha=get_git_commit_sha(),
    )
    guard_destructive_db_from_args(
        args,
        script_name=_SCRIPT,
        operation="Surrey blank-applicant recovery canary (exactly 25 rows)",
        nominal_class=SafetyClass.C,
    )

    source_rows = fetch_official_source_rows()
    session = get_session()
    try:
        result = execute_canary(
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
