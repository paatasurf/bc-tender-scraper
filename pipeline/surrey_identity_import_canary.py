"""Read-only, aggregate-only planner for the Surrey identity-aware import
adapter (PR-EN1F-1, db.surrey_permit_import).

Models the full three-tier decision tree that
``upsert_surrey_permit_identity_aware`` would take for each incoming row
-- update via official_source_id, update via not-yet-bridged legacy
prefix, or insert -- without ever writing to the database. Never returns
raw ids, official PermitNumbers, applicant text, addresses, or any other
row-level payload -- only aggregate counts and a SHA-256 plan digest.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Permit
from db.surrey_permit_import import legacy_key
from scraper.utils import clean_text

ARTIFACT_SCHEMA_VERSION = 1

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "SurreyIdentityImportCanaryError",
    "compute_plan_digest",
    "plan_surrey_identity_import",
]


class SurreyIdentityImportCanaryError(ValueError):
    """Raised when a safe, deterministic import plan cannot be built."""


def compute_plan_digest(entries: Iterable[tuple[int, str, str]]) -> str:
    """Hash the exact planned-action set without returning its contents.
    ``entries`` are (permit_id, outcome, official_permit_number) --
    ``permit_id`` is 0 for a planned insert (no row exists yet)."""
    tokens: set[str] = set()
    for permit_id, outcome, official_permit_number in entries:
        if type(permit_id) is not int or permit_id < 0:
            raise SurreyIdentityImportCanaryError(
                "plan entry permit id must be a non-negative int"
            )
        official = clean_text(official_permit_number)
        if not official:
            raise SurreyIdentityImportCanaryError(
                "plan entry official permit number must be non-empty"
            )
        evidence = hashlib.sha256(f"{outcome}:{official}".encode("utf-8")).hexdigest()
        tokens.add(f"{permit_id}:{evidence}")
    return hashlib.sha256(",".join(sorted(tokens)).encode("utf-8")).hexdigest()


def plan_surrey_identity_import(
    session: Session,
    *,
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return the aggregate-only import plan. Never returns candidates,
    raw permit ids, PermitNumbers, applicant text, or any other row-level
    data."""
    rows = list(rows)

    production_rows = session.execute(
        select(
            Permit.id,
            Permit.external_id,
            Permit.official_source_id,
            Permit.applicant,
        ).where(Permit.source == "surrey")
    ).all()

    by_official: dict[str, list[Any]] = {}
    by_unbridged_legacy: dict[str, list[Any]] = {}
    for production_row in production_rows:
        official_id = clean_text(production_row.official_source_id)
        if official_id:
            by_official.setdefault(official_id, []).append(production_row)
            continue
        legacy_external_id = clean_text(production_row.external_id)
        if legacy_external_id:
            by_unbridged_legacy.setdefault(legacy_external_id, []).append(
                production_row
            )

    invalid_rows = 0
    duplicate_source_rows = 0
    planned_updates = 0
    planned_inserts = 0
    duplicate_risk = 0
    blank_applicant_preserved = 0
    entries: list[tuple[int, str, str]] = []
    seen_official_numbers: set[str] = set()

    for row in rows:
        official_number = clean_text(row.get("external_id"))
        if not official_number:
            invalid_rows += 1
            continue
        if official_number in seen_official_numbers:
            duplicate_source_rows += 1
            continue
        seen_official_numbers.add(official_number)

        incoming_applicant = clean_text(row.get("applicant"))

        official_matches = by_official.get(official_number)
        if official_matches:
            if len(official_matches) > 1:
                duplicate_risk += 1
                continue
            planned_updates += 1
            if not incoming_applicant and clean_text(official_matches[0].applicant):
                blank_applicant_preserved += 1
            entries.append((int(official_matches[0].id), "update", official_number))
            continue

        key = legacy_key(official_number)
        legacy_matches = by_unbridged_legacy.get(key) if key is not None else None
        if legacy_matches:
            if len(legacy_matches) > 1:
                duplicate_risk += 1
                continue
            planned_updates += 1
            if not incoming_applicant and clean_text(legacy_matches[0].applicant):
                blank_applicant_preserved += 1
            entries.append((int(legacy_matches[0].id), "update", official_number))
            continue

        planned_inserts += 1
        entries.append((0, "insert", official_number))

    # duplicate_risk rows are deliberately excluded from `entries` -- an
    # ambiguous match is never planned as a concrete action.
    if planned_updates + planned_inserts != len(entries):
        raise SurreyIdentityImportCanaryError("plan count invariant failed")

    return {
        "counts": {
            "source_total": len(rows),
            "invalid_rows": invalid_rows,
            "duplicate_source_rows": duplicate_source_rows,
            "production_total": len(production_rows),
            "planned_updates": planned_updates,
            "planned_inserts": planned_inserts,
            "duplicate_risk": duplicate_risk,
            "blank_applicant_preserved": blank_applicant_preserved,
        },
        "plan_digest": compute_plan_digest(entries),
    }
