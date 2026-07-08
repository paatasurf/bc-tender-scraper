"""Step 4 — Normalize LinkedIn company names."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.linkedin.paths import NORMALIZED_JSON, RAW_JSON, REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402


def normalize_raw_artifact(raw: dict[str, Any]) -> dict[str, Any]:
    records_out: list[dict[str, Any]] = []
    for rec in raw.get("records") or []:
        name = (rec.get("company_name") or "").strip()
        key = normalize_vendor_name(name)
        records_out.append(
            {
                **rec,
                "normalized_name_key": key,
                "normalization_source_name": name,
            }
        )
    return {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_companies_normalized",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_artifact": raw.get("artifact_type"),
        "source_generated_at": raw.get("generated_at"),
        "read_only": True,
        "db_writes": False,
        "record_count": len(records_out),
        "records": records_out,
    }


def load_raw(path: Path | None = None) -> dict[str, Any]:
    path = path or RAW_JSON
    return json.loads(path.read_text(encoding="utf-8"))


def write_normalized_artifact(artifact: dict[str, Any], path: Path | None = None) -> Path:
    path = path or NORMALIZED_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    return path


def run_normalize(*, raw_path: Path | None = None, out_path: Path | None = None) -> dict[str, Any]:
    raw = load_raw(raw_path)
    normalized = normalize_raw_artifact(raw)
    write_normalized_artifact(normalized, out_path)
    return normalized
