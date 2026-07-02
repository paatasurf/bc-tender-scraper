"""Permit lifecycle source status — reserved for true municipal status sources only."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Vancouver COV open-data `issued-building-permits` has no lifecycle status field.
# `permitcategory` is work complexity, not Issued/Finaled/Cancelled — do NOT map it.
# Surrey ArcGIS `IssuedBuildingPermits` likewise has no PermitStatus in the live feed.
# Future investigation: PLPOS (Vancouver permit lifecycle / inspection system).
FUTURE_STATUS_SOURCES: dict[str, dict[str, str]] = {
    "vancouver": {
        "candidate": "PLPOS",
        "status": "backlog",
        "note": "Investigate PLPOS or other COV internal/export source for Finaled/Issued/Cancelled.",
    },
    "surrey": {
        "candidate": "TBD",
        "status": "backlog",
        "note": "Live ArcGIS feed has no status field; identify alternate source if available.",
    },
}


def count_source_status_state(session: Session, *, source: str) -> dict[str, int]:
    row = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE source_status_raw <> '') AS source_status_set,
                COUNT(*) FILTER (WHERE source_status_raw = '') AS source_status_empty
            FROM permits
            WHERE source = :source
            """
        ),
        {"source": source},
    ).one()
    return dict(row._mapping)


def backfill_permit_source_status(
    session: Session,
    *,
    only_empty: bool = True,
    sources: tuple[str, ...] = ("vancouver", "surrey"),
) -> dict[str, Any]:
    """No-op until a verified lifecycle status source is wired (PLPOS backlog)."""
    cities: dict[str, dict[str, int]] = {}
    for source in sources:
        state = count_source_status_state(session, source=source)
        cities[source] = {
            "updated": 0,
            "skipped_no_source": state["total"],
            "before_set": state["source_status_set"],
            "before_empty": state["source_status_empty"],
            "after_set": state["source_status_set"],
            "after_empty": state["source_status_empty"],
        }

    return {
        "status": "no_status_source_configured",
        "message": (
            "source_status_raw is not populated from scraper APIs. "
            "permitcategory and PermitStatus are work-type fields, not lifecycle statuses. "
            "Awaiting PLPOS / verified municipal status source."
        ),
        "only_empty": only_empty,
        "future_status_sources": FUTURE_STATUS_SOURCES,
        "cities": cities,
        "totals": {
            "updated": 0,
            "after_set": sum(c["after_set"] for c in cities.values()),
            "after_empty": sum(c["after_empty"] for c in cities.values()),
        },
    }
