"""Progress tracking for resumable LinkedIn batch runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from research.linkedin.paths import PROGRESS_JSON


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_progress() -> dict[str, Any]:
    if not PROGRESS_JSON.is_file():
        return {}
    return json.loads(PROGRESS_JSON.read_text(encoding="utf-8"))


def init_progress(*, total_queue: int) -> dict[str, Any]:
    existing = load_progress()
    if existing.get("total_queue") == total_queue and existing.get("next_offset"):
        return existing
    return {
        "schema_version": "1.0.0",
        "artifact_type": "linkedin_batch_progress",
        "read_only": True,
        "db_writes": False,
        "started_at": existing.get("started_at") or _now(),
        "updated_at": _now(),
        "finished_at": None,
        "total_queue": total_queue,
        "companies_completed": existing.get("companies_completed", 0),
        "remaining": total_queue - existing.get("companies_completed", 0),
        "next_offset": existing.get("next_offset", 0),
        "last_processed": existing.get("last_processed"),
        "errors": existing.get("errors") or [],
    }


def save_progress(progress: dict[str, Any]) -> None:
    progress["updated_at"] = _now()
    progress["remaining"] = max(
        0,
        int(progress.get("total_queue", 0)) - int(progress.get("companies_completed", 0)),
    )
    PROGRESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_JSON.write_text(json.dumps(progress, indent=2, default=str), encoding="utf-8")


def resolve_offset(explicit: int | None, progress: dict[str, Any]) -> int:
    if explicit is not None:
        return max(0, explicit)
    return int(progress.get("next_offset") or 0)


def record_processed(
    progress: dict[str, Any],
    *,
    company_name: str,
    normalized_name_key: str,
    linkedin_url: str,
    status: str,
    error: str | None = None,
    permanent: bool = False,
    batch_offset: int,
) -> None:
    progress["companies_completed"] = int(progress.get("companies_completed", 0)) + 1
    progress["next_offset"] = batch_offset + 1
    progress["last_processed"] = {
        "company_name": company_name,
        "normalized_name_key": normalized_name_key,
        "linkedin_url": linkedin_url,
        "status": status,
        "processed_at": _now(),
        "error": error,
    }
    if error:
        progress.setdefault("errors", []).append(
            {
                "company_name": company_name,
                "linkedin_url": linkedin_url,
                "error": error,
                "permanent": permanent,
                "at": _now(),
            }
        )
    save_progress(progress)


def mark_finished(progress: dict[str, Any]) -> None:
    progress["finished_at"] = _now()
    progress["remaining"] = max(
        0,
        int(progress.get("total_queue", 0)) - int(progress.get("companies_completed", 0)),
    )
    save_progress(progress)
