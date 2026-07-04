"""Dry-run artifact provenance for Class C registry apply operations."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"


def get_git_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def get_schema_migration_version() -> str:
    """Fingerprint of applied migration SQL files on disk (repo state)."""
    if not MIGRATIONS_DIR.is_dir():
        return "unknown"
    parts: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        parts.append(f"{path.name}:{digest}")
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone().isoformat()


def _build_fingerprint_payload(session: Session) -> dict[str, Any]:
    counts = {
        "companies": int(session.execute(text("SELECT COUNT(*) FROM companies")).scalar_one()),
        "permits": int(session.execute(text("SELECT COUNT(*) FROM permits")).scalar_one()),
        "company_applicant_aliases": int(
            session.execute(text("SELECT COUNT(*) FROM company_applicant_aliases")).scalar_one()
        ),
        "company_canonical_merge_runs": int(
            session.execute(text("SELECT COUNT(*) FROM company_canonical_merge_runs")).scalar_one()
        ),
    }

    max_updated_at = {
        "companies": _iso(
            session.execute(text("SELECT MAX(updated_at) FROM companies")).scalar_one()
        ),
        "permits_scraped_at": _iso(
            session.execute(text("SELECT MAX(scraped_at) FROM permits")).scalar_one()
        ),
        "permits_status_changed_at": _iso(
            session.execute(text("SELECT MAX(status_changed_at) FROM permits")).scalar_one()
        ),
        "company_applicant_aliases_created_at": _iso(
            session.execute(text("SELECT MAX(created_at) FROM company_applicant_aliases")).scalar_one()
        ),
        "company_canonical_merge_runs_started_at": _iso(
            session.execute(text("SELECT MAX(started_at) FROM company_canonical_merge_runs")).scalar_one()
        ),
    }

    identity_rows = session.execute(
        text(
            """
            SELECT id, COALESCE(canonical_merge_method, '') AS canonical_merge_method
            FROM companies
            ORDER BY id
            """
        )
    ).all()
    parts: list[str] = []
    for row in identity_rows:
        if hasattr(row, "_mapping"):
            mapping = row._mapping
            parts.append(f"{mapping['id']}:{mapping['canonical_merge_method']}")
        elif hasattr(row, "id"):
            parts.append(f"{row.id}:{row.canonical_merge_method}")
        else:
            parts.append(f"{row[0]}:{row[1]}")
    identity_blob = "|".join(parts)
    identity_checksum = hashlib.sha256(identity_blob.encode("utf-8")).hexdigest()[:16]

    return {
        "counts": counts,
        "max_updated_at": max_updated_at,
        "identity_checksum": identity_checksum,
        "schema_migration_version": get_schema_migration_version(),
    }


def compute_dataset_fingerprint(session: Session) -> str:
    payload = _build_fingerprint_payload(session)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_dry_run_provenance(session: Session) -> dict[str, Any]:
    details = _build_fingerprint_payload(session)
    return {
        "git_commit_sha": get_git_commit_sha(),
        "dataset_fingerprint": compute_dataset_fingerprint(session),
        "fingerprint_details": details,
    }


def attach_dry_run_provenance(report: dict[str, Any], session: Session) -> dict[str, Any]:
    enriched = dict(report)
    enriched["dry_run_provenance"] = build_dry_run_provenance(session)
    return enriched


def verify_dry_run_artifact(*, session: Session, report_path: Path) -> None:
    """Refuse apply when dry-run artifact is stale vs current commit or dataset."""
    if not report_path.is_file():
        print(
            f"[db_safety] dry-run is stale — regenerate before apply.\n"
            f"  Missing artifact: {report_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"[db_safety] dry-run is stale — regenerate before apply.\n"
            f"  Invalid JSON in {report_path}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    provenance = payload.get("dry_run_provenance")
    if not isinstance(provenance, dict):
        print(
            "[db_safety] dry-run is stale — regenerate before apply.\n"
            "  Artifact lacks dry_run_provenance (re-run --report after upgrade).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    expected_sha = get_git_commit_sha()
    recorded_sha = str(provenance.get("git_commit_sha", ""))
    if recorded_sha != expected_sha:
        print(
            "[db_safety] dry-run is stale — regenerate before apply.\n"
            f"  git commit mismatch: artifact={recorded_sha} current={expected_sha}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    expected_fp = compute_dataset_fingerprint(session)
    recorded_fp = str(provenance.get("dataset_fingerprint", ""))
    if recorded_fp != expected_fp:
        print(
            "[db_safety] dry-run is stale — regenerate before apply.\n"
            f"  dataset fingerprint mismatch: artifact={recorded_fp} current={expected_fp}",
            file=sys.stderr,
        )
        raise SystemExit(1)
