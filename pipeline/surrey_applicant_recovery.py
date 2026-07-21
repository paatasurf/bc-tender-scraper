"""Planning and tightly-scoped writing for Surrey applicant recovery.

The City of Surrey's current ArcGIS layer exposes a longer ``PermitNumber``
than the legacy 16-character value stored in production.  This module builds
an aggregate-only recovery plan by joining the exact legacy prefix of the
official identifier to existing Surrey permits.

The planning function is read-only and never returns identifiers or applicant
text. The apply function is the separate, digest-pinned writer core: it can
update only a still-blank Permit.applicant for a bounded deterministic
candidate set and deliberately never owns the transaction.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from db.models import Permit
from scraper.utils import clean_text

ARTIFACT_SCHEMA_VERSION = 2
LEGACY_EXTERNAL_ID_LENGTH = 16
RECOMMENDED_CANARY_LIMIT = 25
_LEGACY_ID_RE = re.compile(r"^\d{2}-\d{6}-\d{3}-\d{2}$")
_CURRENT_ID_RE = re.compile(
    r"^\d{2}-\d{6}-\d{3}-\d{2}[^A-Za-z0-9-]"
    r"(?:[A-Za-z]{2}|[A-Za-z]\d(?:[A-Za-z]{2})?)$"
)

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "LEGACY_EXTERNAL_ID_LENGTH",
    "RECOMMENDED_CANARY_LIMIT",
    "SurreyApplicantRecoveryError",
    "apply_surrey_applicant_recovery",
    "compute_recovery_digest",
    "plan_surrey_applicant_recovery",
]


class SurreyApplicantRecoveryError(ValueError):
    """Raised when a safe, deterministic recovery plan cannot be built."""


@dataclass(frozen=True)
class _RecoveryCandidate:
    permit_id: int
    legacy_external_id: str
    source_permit_number: str
    applicant: str


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


def _build_recovery_plan(
    session: Session,
    *,
    source_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[_RecoveryCandidate]]:
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
    candidates: list[_RecoveryCandidate] = []

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
            candidates.append(
                _RecoveryCandidate(
                    permit_id=int(row.id),
                    legacy_external_id=production_key,
                    source_permit_number=source_permit_number,
                    applicant=source_applicant,
                )
            )

    source_keys = set(safe_source_by_key)
    source_only = len(source_keys - production_keys)
    production_only = len(production_keys - source_keys)

    if recoverable != len(candidates):
        raise SurreyApplicantRecoveryError("candidate count invariant failed")
    if overlapping != recoverable + already_populated + overlap_source_missing:
        raise SurreyApplicantRecoveryError("overlap count invariant failed")

    candidate_entries = [
        (candidate.permit_id, candidate.source_permit_number, candidate.applicant)
        for candidate in candidates
    ]
    canary_candidates = candidates[:RECOMMENDED_CANARY_LIMIT]
    canary_entries = [
        (candidate.permit_id, candidate.source_permit_number, candidate.applicant)
        for candidate in canary_candidates
    ]
    report = {
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
        "candidate_count": len(candidates),
        "candidate_set_digest": compute_recovery_digest(candidate_entries),
        "recommended_canary_limit": RECOMMENDED_CANARY_LIMIT,
        "canary_candidate_count": len(canary_candidates),
        "canary_candidate_set_digest": compute_recovery_digest(canary_entries),
    }
    return report, candidates


def plan_surrey_applicant_recovery(
    session: Session,
    *,
    source_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the aggregate-only recovery plan; never expose candidates."""
    report, _candidates = _build_recovery_plan(session, source_rows=source_rows)
    return report


def apply_surrey_applicant_recovery(
    session: Session,
    *,
    source_rows: Iterable[Mapping[str, Any]],
    candidate_limit: int,
    expected_candidate_set_digest: str,
) -> dict[str, Any]:
    """Apply one bounded, digest-pinned, blank-only recovery batch.

    This function deliberately does not commit, flush, or roll back. The
    future Class-C runner owns the transaction and must roll it back on any
    exception. Every update is constrained by id, source, legacy external id,
    and an applicant that is still NULL/blank; a concurrent change therefore
    fails closed via the exact row-count check.
    """
    if type(candidate_limit) is not int or candidate_limit <= 0:
        raise SurreyApplicantRecoveryError("candidate_limit must be a positive int")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_candidate_set_digest):
        raise SurreyApplicantRecoveryError(
            "expected_candidate_set_digest must be lowercase SHA-256 hex"
        )

    report, candidates = _build_recovery_plan(session, source_rows=source_rows)
    selected = candidates[:candidate_limit]
    selected_entries = [
        (candidate.permit_id, candidate.source_permit_number, candidate.applicant)
        for candidate in selected
    ]
    actual_digest = compute_recovery_digest(selected_entries)
    if actual_digest != expected_candidate_set_digest:
        raise SurreyApplicantRecoveryError(
            "candidate set changed since the reviewed dry-run artifact"
        )

    for candidate in selected:
        result = session.execute(
            update(Permit)
            .where(
                Permit.id == candidate.permit_id,
                Permit.source == "surrey",
                Permit.external_id == candidate.legacy_external_id,
                or_(Permit.applicant.is_(None), Permit.applicant == ""),
            )
            .values(applicant=candidate.applicant)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise SurreyApplicantRecoveryError(
                "blank-only applicant update did not affect exactly one row"
            )

    return {
        "eligible_count": report["candidate_count"],
        "selected_count": len(selected),
        "updated_count": len(selected),
        "candidate_limit": candidate_limit,
        "candidate_set_digest": actual_digest,
    }
