#!/usr/bin/env python3
"""Unified production data coverage audit (Class A -- no database writes)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: E402,F401
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from db.connection import get_engine  # noqa: E402
from db.db_safety import guard_readonly_db_from_args  # noqa: E402
from db.merge_dry_run_provenance import get_git_commit_sha  # noqa: E402
from pipeline.data_coverage_audit import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    DataCoverageAuditError,
    audit_data_coverage,
)

DEFAULT_ARTIFACT_PATH = ROOT / "exports" / "data_coverage_audit_class_a.json"
TRANSACTION_MODE = "REPEATABLE READ, READ ONLY"


def _resolve_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DataCoverageAuditError("--as-of must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise DataCoverageAuditError("--as-of must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def run_audit(
    engine: Engine, *, artifact_path: Path | None, as_of: datetime
) -> dict[str, Any]:
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise DataCoverageAuditError("as_of must be a timezone-aware datetime")
    conn = engine.connect()
    trans = conn.begin()
    session: Session | None = None
    try:
        conn.execute(text(f"SET TRANSACTION ISOLATION LEVEL {TRANSACTION_MODE}"))
        session = Session(bind=conn)
        report = audit_data_coverage(session, as_of=as_of)
    finally:
        if session is not None:
            session.close()
        trans.rollback()
        conn.close()

    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": get_git_commit_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_mode": TRANSACTION_MODE,
        **report,
    }
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-production", action="store_true")
    parser.add_argument("--artifact-path", type=Path, default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--as-of", default=None, help="Injected ISO-8601 timestamp")
    args = parser.parse_args()
    try:
        as_of = _resolve_as_of(args.as_of)
    except DataCoverageAuditError as exc:
        parser.error(str(exc))
    guard_readonly_db_from_args(args, script_name=Path(__file__).name)
    engine = get_engine()
    try:
        artifact = run_audit(engine, artifact_path=args.artifact_path, as_of=as_of)
    finally:
        engine.dispose()
    print(json.dumps(artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
