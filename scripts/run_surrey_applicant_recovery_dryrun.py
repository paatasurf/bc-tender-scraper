#!/usr/bin/env python3
"""Surrey applicant recovery dry-run (Class A -- no database writes).

Fetches only PermitNumber and ApplicantOrganization from Surrey's official
ArcGIS layer, then builds an aggregate-only plan inside one explicit
REPEATABLE READ, READ ONLY database transaction. There is no --apply or
--allow-production mode.
"""

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
from pipeline.surrey_applicant_recovery import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    plan_surrey_applicant_recovery,
)
from scraper.config import SURREY_PERMITS_API  # noqa: E402
from scraper.utils import create_session, polite_api_get  # noqa: E402

DEFAULT_ARTIFACT_PATH = ROOT / "exports" / "surrey_applicant_recovery_dryrun.json"
TRANSACTION_MODE = "REPEATABLE READ, READ ONLY"
SOURCE_FIELDS = "PermitNumber,ApplicantOrganization"
DEFAULT_PAGE_SIZE = 500


def fetch_official_source_rows() -> list[dict[str, Any]]:
    """Fetch the full current ArcGIS window without filtering on address."""
    http = create_session()
    try:
        metadata = polite_api_get(http, SURREY_PERMITS_API, params={"f": "pjson"})
        metadata.raise_for_status()
        metadata_payload = metadata.json()
        if metadata_payload.get("error"):
            raise RuntimeError("Surrey ArcGIS metadata request failed")
        raw_page_size = int(metadata_payload.get("maxRecordCount") or DEFAULT_PAGE_SIZE)
        if raw_page_size <= 0:
            raise RuntimeError("Surrey ArcGIS returned an invalid page size")
        page_size = min(raw_page_size, DEFAULT_PAGE_SIZE)

        count_response = polite_api_get(
            http,
            f"{SURREY_PERMITS_API}/query",
            params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
        )
        count_response.raise_for_status()
        count_payload = count_response.json()
        if count_payload.get("error"):
            raise RuntimeError("Surrey ArcGIS count request failed")
        expected_count = count_payload.get("count")
        if type(expected_count) is not int or expected_count < 0:
            raise RuntimeError("Surrey ArcGIS returned an invalid record count")

        rows: list[dict[str, Any]] = []
        offset = 0
        while len(rows) < expected_count:
            response = polite_api_get(
                http,
                f"{SURREY_PERMITS_API}/query",
                params={
                    "where": "1=1",
                    "outFields": SOURCE_FIELDS,
                    "returnGeometry": "false",
                    "resultOffset": offset,
                    "resultRecordCount": page_size,
                    "orderByFields": "PermitNumber",
                    "f": "json",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError("Surrey ArcGIS query failed")
            features = payload.get("features") or []
            for feature in features:
                attrs = feature.get("attributes") or {}
                rows.append(
                    {
                        "PermitNumber": attrs.get("PermitNumber"),
                        "ApplicantOrganization": attrs.get("ApplicantOrganization"),
                    }
                )
            raw_count = len(features)
            if raw_count == 0:
                raise RuntimeError(
                    "Surrey ArcGIS pagination ended before the advertised count"
                )
            offset += raw_count
            if len(rows) > expected_count:
                raise RuntimeError(
                    "Surrey ArcGIS pagination exceeded the advertised count"
                )
        if len(rows) != expected_count:
            raise RuntimeError("Surrey ArcGIS record count invariant failed")
        return rows
    finally:
        http.close()


def run_dry_run(
    engine: Engine,
    *,
    source_rows: list[dict[str, Any]],
    artifact_path: Path | None,
) -> dict[str, Any]:
    conn = engine.connect()
    trans = conn.begin()
    session: Session | None = None
    try:
        conn.execute(text(f"SET TRANSACTION ISOLATION LEVEL {TRANSACTION_MODE}"))
        session = Session(bind=conn)
        report = plan_surrey_applicant_recovery(
            session,
            source_rows=source_rows,
        )
    finally:
        if session is not None:
            session.close()
        trans.rollback()
        conn.close()

    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": get_git_commit_sha(),
        "source": "surrey",
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
    args = parser.parse_args()

    guard_readonly_db_from_args(args, script_name=Path(__file__).name)
    source_rows = fetch_official_source_rows()
    engine = get_engine()
    try:
        artifact = run_dry_run(
            engine,
            source_rows=source_rows,
            artifact_path=args.artifact_path,
        )
    finally:
        engine.dispose()
    print(json.dumps(artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
