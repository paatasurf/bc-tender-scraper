"""Read-only planning for Surrey historical applicant recovery.

The City of Surrey's current ArcGIS layer exposes a longer ``PermitNumber``
than the legacy 16-character value stored in production.  This module builds
an aggregate-only recovery plan by joining the exact legacy prefix of the
official identifier to existing ``source='surrey'`` permits.  It never writes
to the database and never returns identifiers or applicant text.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Permit
from scraper.utils import clean_text

ARTIFACT_SCHEMA_VERSION = 1
LEGACY_EXTERNAL_ID_LENGTH = 16
_LEGACY_ID_RE = re.compile(r"^\d{2}-\d{6}-\d{3}-\d{2}$")
_CURRENT_ID_RE = re.compile(
    r"^\d{2}-\d{6}-\d{3}-\d{2}[^A-Za-z0-9-][A-Za-z]{1,3}(?:\d{2})?$"
)

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "LEGACY_EXTERNAL_ID_LENGTH",
    "SurreyApplicantRecoveryError",
    "compute_recovery_digest",
    "plan_surrey_applicant_recovery",
]


class SurreyApplicantRecoveryError(ValueError):
    """Raised when a safe, deterministic recovery plan cannot be built."""


def _legacy_key(permit_number: str) -> str | None:
    value = clean_text(permit_number)
    if not _CURRENT_ID_RE.fullmatch(value):
        return None
    prefix = value[:LEGACY_EXTERNAL_ID_LENGTH]
    if not _LEGACY_ID_RE.fullmatch(prefix):
        return None
    return prefix


def compute_recovery_digest(entries: Iterable[tuple[int, str, str]]) -> str:
    """Hash the exact candidate/evidence set without returning its contents."""
    tokens: set[str] = set()
    for permit_id, source_permit_number, applicant in entries:
        if type(permit_id) is not int or permit_id <= 0:
            raise SurreyApplicantRecoveryError(
                "candidate permit id must be positive int"
            )
        source_id = clean_text(source_permit_number)
        raw_applicant = clean_text(applicant)
        if not source_id or not raw_applicant:
            raise SurreyApplicantRecoveryError(
                "candidate source id and applicant must be non-empty"
            )
        evidence = hashlib.sha256(
            f"{source_id}\x1f{raw_applicant}".encode("utf-8")
        ).hexdigest()
        tokens.add(f"{permit_id}:{evidence}")
    return hashlib.sha256(",".join(sorted(tokens)).encode("utf-8")).hexdigest()


def plan_surrey_applicant_recovery(
    session: Session,
    *,
    source_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an aggregate-only blank-applicant recovery plan.

    ``source_rows`` must contain only the official ``PermitNumber`` and
    ``ApplicantOrganization`` evidence needed by this plan.  Ambiguous source
    prefixes are reported and excluded.  Existing non-empty applicants are
    never candidates.
    """
    rows = list(source_rows)
    source_ids: list[str] = []
    valid_rows: list[tuple[str, str, str]] = []
    invalid_source_ids = 0
    source_missing_applicant = 0

    for row in rows:
        permit_number = clean_text(row.get("PermitNumber"))
        applicant = clean_text(row.get("ApplicantOrganization"))
        source_ids.append(permit_number)
        key = _legacy_key(permit_number)
        if key is None:
            invalid_source_ids += 1
            continue
        if not applicant:
            source_missing_applicant += 1
        valid_rows.append((key, permit_number, applicant))

    exact_counts = Counter(source_id for source_id in source_ids if source_id)
    duplicate_source_ids = sum(
        count - 1 for count in exact_counts.values() if count > 1
    )
    prefix_counts = Counter(key for key, _, _ in valid_rows)
    ambiguous_prefixes = {key for key, count in prefix_counts.items() if count > 1}
    duplicate_legacy_prefix_rows = sum(
        count - 1 for count in prefix_counts.values() if count > 1
    )

    safe_source_by_key = {
        key: (permit_number, applicant)
        for key, permit_number, applicant in valid_rows
        if key not in ambiguous_prefixes
    }

    production_rows = session.execute(
        select(Permit.id, Permit.external_id, Permit.applicant)
        .where(Permit.source == "surrey")
        .order_by(Permit.id)
    ).all()
    production_key_counts = Counter(
        clean_text(row.external_id) for row in production_rows
    )
    ambiguous_production_keys = {
        key for key, count in production_key_counts.items() if key and count > 1
    }

    overlapping = 0
    recoverable = 0
    already_populated = 0
    overlap_source_missing = 0
    candidate_entries: list[tuple[int, str, str]] = []

    production_keys: set[str] = set()
    for row in production_rows:
        production_key = clean_text(row.external_id)
        if production_key:
            production_keys.add(production_key)
        evidence = safe_source_by_key.get(production_key)
        if evidence is None or production_key in ambiguous_production_keys:
            continue
        overlapping += 1
        source_permit_number, source_applicant = evidence
        if not source_applicant:
            overlap_source_missing += 1
        elif clean_text(row.applicant):
            already_populated += 1
        else:
            recoverable += 1
            candidate_entries.append(
                (int(row.id), source_permit_number, source_applicant)
            )

    source_keys = set(safe_source_by_key)
    source_only = len(source_keys - production_keys)
    production_only = len(production_keys - source_keys)

    if recoverable != len(candidate_entries):
        raise SurreyApplicantRecoveryError("candidate count invariant failed")
    if overlapping != recoverable + already_populated + overlap_source_missing:
        raise SurreyApplicantRecoveryError("overlap count invariant failed")

    return {
        "counts": {
            "source_total": len(rows),
            "source_distinct_ids": len(set(exact_counts)),
            "invalid_source_ids": invalid_source_ids,
            "duplicate_source_ids": duplicate_source_ids,
            "source_missing_applicant": source_missing_applicant,
            "source_valid_legacy_keys": len(prefix_counts),
            "ambiguous_legacy_prefixes": len(ambiguous_prefixes),
            "duplicate_legacy_prefix_rows": duplicate_legacy_prefix_rows,
            "production_total": len(production_rows),
            "production_distinct_external_ids": len(production_keys),
            "ambiguous_production_external_ids": len(ambiguous_production_keys),
            "overlapping_rows": overlapping,
            "recoverable_blank_applicant": recoverable,
            "already_populated_applicant": already_populated,
            "overlap_source_missing_applicant": overlap_source_missing,
            "source_only_keys": source_only,
            "production_only_keys": production_only,
        },
        "candidate_count": len(candidate_entries),
        "candidate_set_digest": compute_recovery_digest(candidate_entries),
    }
