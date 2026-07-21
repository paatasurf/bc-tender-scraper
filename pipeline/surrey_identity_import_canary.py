"""Read-only, aggregate-only planner for the Surrey identity-aware import
adapter (PR-EN1F-1, db.surrey_permit_import), plus the fixed, deterministic
Class-C import canary writer core (PR-EN1F-3).

Models the full three-tier decision tree that
``upsert_surrey_permit_identity_aware`` would take for each incoming row
-- update via official_source_id, update via not-yet-bridged legacy
prefix, or insert -- without ever writing to the database. The planner
(``plan_surrey_identity_import``) never returns raw ids, official
PermitNumbers, applicant text, addresses, or any other row-level payload
-- only aggregate counts and a SHA-256 plan digest.

``apply_surrey_identity_import_canary`` is the separate, digest-pinned
writer core: it re-derives the full plan, refuses on any drift against a
previously reviewed ``plan_digest``, then applies a fixed, deterministic
slice (by default 20 update candidates + 5 insert candidates), selected
by sorting each outcome group by its own canonical key -- the full
official PermitNumber, in lexicographic order -- not by the order the
source rows happened to be fetched in. It deliberately never owns the
transaction -- that belongs to a Class-C runner.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Permit
from db.surrey_permit_import import legacy_key, upsert_surrey_permits_identity_aware
from scraper.utils import clean_text

ARTIFACT_SCHEMA_VERSION = 1
FIXED_UPDATE_LIMIT = 20
FIXED_INSERT_LIMIT = 5

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "FIXED_INSERT_LIMIT",
    "FIXED_UPDATE_LIMIT",
    "SurreyIdentityImportCanaryError",
    "apply_surrey_identity_import_canary",
    "compute_plan_digest",
    "plan_surrey_identity_import",
]


class SurreyIdentityImportCanaryError(ValueError):
    """Raised when a safe, deterministic import plan cannot be built."""


@dataclass(frozen=True)
class _ImportCandidate:
    permit_id: int  # 0 for a planned insert -- no row exists yet
    outcome: str  # "update" | "insert"
    official_permit_number: str
    row: Mapping[str, Any]


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


def _build_import_plan(
    session: Session,
    *,
    rows: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[_ImportCandidate]]:
    """Build the aggregate-only, fail-closed import plan, plus the
    concrete (private) candidate list that a Class-C writer needs to
    apply a bounded slice. ``rows`` order is preserved in ``candidates``,
    matching the deterministic ArcGIS fetch order (already sorted by
    PermitNumber)."""
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
    candidates: list[_ImportCandidate] = []
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
            permit_id = int(official_matches[0].id)
            entries.append((permit_id, "update", official_number))
            candidates.append(
                _ImportCandidate(
                    permit_id=permit_id,
                    outcome="update",
                    official_permit_number=official_number,
                    row=row,
                )
            )
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
            permit_id = int(legacy_matches[0].id)
            entries.append((permit_id, "update", official_number))
            candidates.append(
                _ImportCandidate(
                    permit_id=permit_id,
                    outcome="update",
                    official_permit_number=official_number,
                    row=row,
                )
            )
            continue

        planned_inserts += 1
        entries.append((0, "insert", official_number))
        candidates.append(
            _ImportCandidate(
                permit_id=0,
                outcome="insert",
                official_permit_number=official_number,
                row=row,
            )
        )

    # duplicate_risk rows are deliberately excluded from `entries` -- an
    # ambiguous match is never planned as a concrete action.
    if planned_updates + planned_inserts != len(entries):
        raise SurreyIdentityImportCanaryError("plan count invariant failed")
    if len(entries) != len(candidates):
        raise SurreyIdentityImportCanaryError("candidate count invariant failed")

    report = {
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
    return report, candidates


def plan_surrey_identity_import(
    session: Session,
    *,
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return the aggregate-only import plan. Never returns candidates,
    raw permit ids, PermitNumbers, applicant text, or any other row-level
    data."""
    report, _candidates = _build_import_plan(session, rows=rows)
    return report


def apply_surrey_identity_import_canary(
    session: Session,
    *,
    rows: Iterable[Mapping[str, Any]],
    expected_plan_digest: str,
    update_limit: int = FIXED_UPDATE_LIMIT,
    insert_limit: int = FIXED_INSERT_LIMIT,
) -> dict[str, Any]:
    """Apply one fixed, deterministic import canary batch.

    Re-derives the full plan from ``rows`` and refuses (fail closed) if
    the full ``plan_digest`` no longer matches ``expected_plan_digest`` --
    any drift anywhere in the full candidate universe, not just the
    selected slice, blocks the write before it starts. Candidates are
    then split into update/insert groups and each group is sorted by its
    own stable canonical key -- the full official ``PermitNumber``
    (``official_permit_number``), in explicit lexicographic order --
    deliberately ignoring the order ``rows`` was given in, so the
    selection is identical no matter what order the ArcGIS source (or any
    other caller) happens to return rows in. The first ``update_limit``
    update candidates and first ``insert_limit`` insert candidates from
    that canonical ordering are applied via
    ``db.surrey_permit_import.upsert_surrey_permits_identity_aware``.

    Deliberately does not commit, flush, or roll back -- the Class-C
    runner owns the transaction and must roll it back on any exception or
    an unexpected updated/inserted count. Does not itself enforce that
    exactly ``update_limit``/``insert_limit`` rows were available or
    applied (fewer candidates than requested are applied as-is); that
    exactness check is the caller's responsibility, matching the
    established pattern of ``apply_permit_official_source_id_bridge``.
    """
    if type(update_limit) is not int or update_limit <= 0:
        raise SurreyIdentityImportCanaryError("update_limit must be a positive int")
    if type(insert_limit) is not int or insert_limit <= 0:
        raise SurreyIdentityImportCanaryError("insert_limit must be a positive int")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_plan_digest or "")):
        raise SurreyIdentityImportCanaryError(
            "expected_plan_digest must be lowercase SHA-256 hex"
        )

    report, candidates = _build_import_plan(session, rows=rows)
    if report["plan_digest"] != expected_plan_digest:
        raise SurreyIdentityImportCanaryError(
            "candidate set changed since the reviewed dry-run artifact"
        )

    # Canonical, input-order-independent selection: sort each outcome
    # group by the full official PermitNumber, not by ArcGIS fetch order.
    updates = sorted(
        (c for c in candidates if c.outcome == "update"),
        key=lambda c: c.official_permit_number,
    )[:update_limit]
    inserts = sorted(
        (c for c in candidates if c.outcome == "insert"),
        key=lambda c: c.official_permit_number,
    )[:insert_limit]
    selected = updates + inserts
    selected_entries = [
        (c.permit_id, c.outcome, c.official_permit_number) for c in selected
    ]
    canary_digest = compute_plan_digest(selected_entries)

    result = upsert_surrey_permits_identity_aware(session, [c.row for c in selected])

    return {
        "eligible_updates": report["counts"]["planned_updates"],
        "eligible_inserts": report["counts"]["planned_inserts"],
        "selected_updates": len(updates),
        "selected_inserts": len(inserts),
        "updated": result["updated"],
        "inserted": result["inserted"],
        "plan_digest": report["plan_digest"],
        "canary_digest": canary_digest,
    }
