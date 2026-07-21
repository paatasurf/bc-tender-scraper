"""Read-only planning for the Surrey official-source-identity bridge.

The City of Surrey's current ArcGIS layer exposes a longer ``PermitNumber``
than the legacy 16-character value stored in ``Permit.external_id``. This
module builds an aggregate-only plan to populate the still-blank
``Permit.official_source_id`` for existing Surrey permits, by joining the
exact 16-character legacy prefix of the official ``PermitNumber`` to the
existing ``Permit.external_id`` -- exact match only, never fuzzy.

This module is planning-only: nothing here ever writes to the database.
A future, separately-reviewed identity-bridge writer (Class-C, digest-
pinned, mirroring ``pipeline.surrey_applicant_recovery``) is what would
actually apply a reviewed plan -- not shipped in this PR.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Permit
from scraper.utils import clean_text

ARTIFACT_SCHEMA_VERSION = 1
LEGACY_EXTERNAL_ID_LENGTH = 16
# Same identity-format contract as pipeline.surrey_applicant_recovery: the
# legacy production key is DD-DDDDDD-DDD-DD; the current official
# PermitNumber is that prefix plus a single non-alphanumeric separator and
# a short letter/letter-digit suffix.
_LEGACY_ID_RE = re.compile(r"^\d{2}-\d{6}-\d{3}-\d{2}$")
_CURRENT_ID_RE = re.compile(
    r"^\d{2}-\d{6}-\d{3}-\d{2}[^A-Za-z0-9-]"
    r"(?:[A-Za-z]{2}|[A-Za-z]\d(?:[A-Za-z]{2})?)$"
)

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "LEGACY_EXTERNAL_ID_LENGTH",
    "PermitOfficialSourceIdBridgeError",
    "compute_bridge_digest",
    "plan_permit_official_source_id_bridge",
]


class PermitOfficialSourceIdBridgeError(ValueError):
    """Raised when a safe, deterministic bridge plan cannot be built."""


@dataclass(frozen=True)
class _BridgeCandidate:
    permit_id: int
    legacy_external_id: str
    source_permit_number: str


def _legacy_key(permit_number: str) -> str | None:
    value = clean_text(permit_number)
    if not _CURRENT_ID_RE.fullmatch(value):
        return None
    prefix = value[:LEGACY_EXTERNAL_ID_LENGTH]
    if not _LEGACY_ID_RE.fullmatch(prefix):
        return None
    return prefix


def compute_bridge_digest(entries: Iterable[tuple[int, str]]) -> str:
    """Hash the exact candidate/evidence set without returning its contents."""
    tokens: set[str] = set()
    for permit_id, source_permit_number in entries:
        if type(permit_id) is not int or permit_id <= 0:
            raise PermitOfficialSourceIdBridgeError(
                "candidate permit id must be a positive int"
            )
        source_id = clean_text(source_permit_number)
        if not source_id:
            raise PermitOfficialSourceIdBridgeError(
                "candidate source permit number must be non-empty"
            )
        evidence = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
        tokens.add(f"{permit_id}:{evidence}")
    return hashlib.sha256(",".join(sorted(tokens)).encode("utf-8")).hexdigest()


def _build_bridge_plan(
    session: Session,
    *,
    source_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[_BridgeCandidate]]:
    """Build the aggregate-only, fail-closed bridge plan.

    ``source_rows`` must contain only the official ``PermitNumber``
    evidence needed by this plan. Ambiguous source prefixes and ambiguous
    production keys are reported and excluded -- never guessed. Permits
    whose ``official_source_id`` is already non-empty are never
    candidates.
    """
    rows = list(source_rows)
    source_ids: list[str] = []
    valid_rows: list[tuple[str, str]] = []  # (legacy_key, full_permit_number)
    invalid_source_ids = 0

    for row in rows:
        permit_number = clean_text(row.get("PermitNumber"))
        source_ids.append(permit_number)
        key = _legacy_key(permit_number)
        if key is None:
            invalid_source_ids += 1
            continue
        valid_rows.append((key, permit_number))

    exact_counts = Counter(sid for sid in source_ids if sid)
    duplicate_source_ids = sum(
        count - 1 for count in exact_counts.values() if count > 1
    )

    prefix_counts = Counter(key for key, _permit_number in valid_rows)
    ambiguous_prefixes = {key for key, count in prefix_counts.items() if count > 1}
    duplicate_legacy_prefix_rows = sum(
        count - 1 for count in prefix_counts.values() if count > 1
    )

    safe_source_by_key = {
        key: permit_number
        for key, permit_number in valid_rows
        if key not in ambiguous_prefixes
    }

    production_rows = session.execute(
        select(Permit.id, Permit.external_id, Permit.official_source_id)
        .where(Permit.source == "surrey")
        .order_by(Permit.id)
    ).all()
    production_key_counts = Counter(
        clean_text(row.external_id) for row in production_rows
    )
    ambiguous_production_keys = {
        key for key, count in production_key_counts.items() if key and count > 1
    }
    duplicate_production_legacy_ids = sum(
        count - 1 for key, count in production_key_counts.items() if key and count > 1
    )

    overlapping = 0
    already_bridged = 0
    candidates: list[_BridgeCandidate] = []

    production_keys: set[str] = set()
    for row in production_rows:
        production_key = clean_text(row.external_id)
        if production_key:
            production_keys.add(production_key)
        evidence = safe_source_by_key.get(production_key)
        if evidence is None or production_key in ambiguous_production_keys:
            continue
        overlapping += 1
        if clean_text(row.official_source_id):
            already_bridged += 1
        else:
            candidates.append(
                _BridgeCandidate(
                    permit_id=int(row.id),
                    legacy_external_id=production_key,
                    source_permit_number=evidence,
                )
            )

    source_keys = set(safe_source_by_key)
    source_only = len(source_keys - production_keys)
    production_only = len(production_keys - source_keys)

    if overlapping != len(candidates) + already_bridged:
        raise PermitOfficialSourceIdBridgeError("overlap count invariant failed")

    candidate_entries = [
        (candidate.permit_id, candidate.source_permit_number)
        for candidate in candidates
    ]
    report = {
        "counts": {
            "source_total": len(rows),
            "source_distinct_full_ids": len(set(exact_counts)),
            "invalid_source_ids": invalid_source_ids,
            "duplicate_source_ids": duplicate_source_ids,
            "source_valid_legacy_keys": len(prefix_counts),
            "ambiguous_legacy_prefixes": len(ambiguous_prefixes),
            "duplicate_legacy_prefix_rows": duplicate_legacy_prefix_rows,
            "production_total": len(production_rows),
            "production_distinct_external_ids": len(production_keys),
            "ambiguous_production_external_ids": len(ambiguous_production_keys),
            "duplicate_production_legacy_ids": duplicate_production_legacy_ids,
            "overlapping_rows": overlapping,
            "existing_nonempty_official_source_id": already_bridged,
            "source_only_keys": source_only,
            "production_only_keys": production_only,
        },
        "candidate_count": len(candidates),
        "candidate_set_digest": compute_bridge_digest(candidate_entries),
    }
    return report, candidates


def plan_permit_official_source_id_bridge(
    session: Session,
    *,
    source_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the aggregate-only bridge plan. Never returns candidates,
    raw permit ids, PermitNumbers, or any other row-level data."""
    report, _candidates = _build_bridge_plan(session, source_rows=source_rows)
    return report
