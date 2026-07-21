"""Read-only Surrey permit resolution-readiness audit (PR-EN1C, Class A).

Reports, in aggregate only, how many ``source='surrey'`` ``Permit`` rows
would be resolvable against *already-existing* ``Company`` rows if a
future controlled backfill ran -- without ever writing anything, and
without ever fuzzy-matching.

Two independently-staged, fail-closed classifications per row:

1. Normalization (``pipeline.surrey_applicant.normalize_surrey_applicant``,
   already fail-closed and fuzzy-free): ``applicant_missing`` (empty raw
   value) / ``normalized_safe`` (a legal-suffix or business-keyword
   organization was safely extracted) / ``normalization_unresolved``
   (ambiguous, a person name, a composite, or otherwise unresolvable).
2. Resolution, only for ``normalized_safe`` rows, against a read-only
   in-memory index of *existing* ``Company`` rows built once from a
   single read-only scan (``CompanyIndex``): ``matched_existing_company``
   (exactly one real company identity, after collapsing an applicant
   alias to its canonical row) / ``ambiguous_existing_company`` (more
   than one distinct real company identity) / ``unmatched_existing_company``
   (none).

Nothing here ever creates a ``Company``, an alias, a review row, or a
registry decision, and nothing here ever fuzzy-matches -- see
``CompanyIndex`` for exactly why ``CompanyResolver`` was not reused
directly for this audit.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    FORCED_CANONICAL_IDS_BY_KEY,
)
from db.models import Company, Permit
from pipeline.company_canonical_merge import resolve_company_name
from pipeline.company_matching import normalize_vendor_name
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.surrey_applicant import (
    STATUS_MISSING,
    STATUS_NORMALIZED_BUSINESS_ADDRESS,
    STATUS_NORMALIZED_LEGAL_SUFFIX,
    STATUS_UNRESOLVED,
    normalize_surrey_applicant,
)

ARTIFACT_SCHEMA_VERSION = 1

BUCKET_APPLICANT_MISSING = "applicant_missing"
BUCKET_NORMALIZED_SAFE = "normalized_safe"
BUCKET_NORMALIZATION_UNRESOLVED = "normalization_unresolved"

OUTCOME_MATCHED = "matched"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_UNMATCHED = "unmatched"

METHOD_DIRECT_KEY = "direct_key"
METHOD_ALIAS_COLLAPSED = "alias_collapsed"
METHOD_FORCED_OVERRIDE = "forced_override"

TIER_HIGH = "high"
TIER_MEDIUM = "medium"

STAGE_NORMALIZATION = "normalization"
STAGE_RESOLUTION = "resolution"

UNCLASSIFIED_ERROR_TYPE = "UnclassifiedError"
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "UNCLASSIFIED_ERROR_TYPE",
    "SurreyResolutionAuditError",
    "CompanyIndex",
    "compute_examined_digest",
    "audit_surrey_permit_resolution",
]


class SurreyResolutionAuditError(ValueError):
    """Raised when the audit cannot be built as a fail-closed, accurate
    aggregate -- currently only a count-invariant mismatch between the
    per-row loop and the returned counts, which should never happen but
    must never be silently reported as valid if it somehow did."""


class CompanyIndex:
    """Read-only, in-memory index of existing ``Company`` identities,
    built from exactly one ``select(Company)`` scan.

    Deliberately reuses only the *pure* building blocks
    ``resolve_company_name``/``normalize_vendor_name`` (no session
    dependency) that ``CompanyResolver`` itself uses for its own exact
    key index, but -- unlike ``CompanyResolver`` -- never applies the
    DBA-family *prefix* expansion it uses on its create path
    (``_collect_candidate_ids``'s ``startswith`` matching), since that is
    a fuzzy heuristic and this audit must never fuzzy-match. Only exact
    canonical-key equality, plus alias-to-canonical collapse, is used.
    """

    def __init__(self, session: Session) -> None:
        self._key_to_ids: dict[str, set[int]] = {}
        self._known_ids: set[int] = set()
        raw_alias_to_canonical: dict[int, int] = {}
        for company in session.scalars(select(Company)).all():
            company_id = int(company.id)
            self._known_ids.add(company_id)
            for candidate in (
                company.name,
                company.display_name,
                company.canonical_vendor_name,
            ):
                key = normalize_vendor_name(candidate or "")
                if key:
                    self._key_to_ids.setdefault(key, set()).add(company_id)
            resolved = resolve_company_name(company.name or "")
            if resolved:
                self._key_to_ids.setdefault(resolved.canonical_key, set()).add(
                    company_id
                )
            if (
                company.entity_role == ENTITY_ROLE_APPLICANT_ALIAS
                and company.canonical_company_id is not None
            ):
                raw_alias_to_canonical[company_id] = int(company.canonical_company_id)

        # Fail-closed: an alias whose canonical_company_id does not point at
        # an actually-loaded Company row (dangling reference -- the
        # canonical row was deleted, remapped, or never existed) must never
        # collapse into a phantom "matched" identity. Such an alias is kept
        # as its own standalone candidate (it is still a real, loaded
        # Company row) rather than silently dropped or force-matched.
        self._alias_to_canonical = {
            alias_id: canonical_id
            for alias_id, canonical_id in raw_alias_to_canonical.items()
            if canonical_id in self._known_ids
        }

    def match(self, organization: str) -> tuple[str, str | None]:
        """Return ``(outcome, method)``. ``outcome`` is one of
        ``matched``/``ambiguous``/``unmatched``; ``method`` is one of
        ``direct_key``/``alias_collapsed``/``forced_override`` when
        ``outcome == "matched"``, else ``None``. Pure read of the
        in-memory index built at construction time -- never touches the
        session again, never raises for a plain string input, never
        creates or mutates anything."""
        parsed = resolve_company_name(organization)
        if parsed is None:
            return OUTCOME_UNMATCHED, None
        if is_probable_person_name(parsed.display_name):
            return OUTCOME_UNMATCHED, None

        key = parsed.canonical_key
        candidate_ids = set(self._key_to_ids.get(key, set()))
        forced_id = FORCED_CANONICAL_IDS_BY_KEY.get(key)
        # Fail-closed: a forced override is only honoured when its target id
        # is an actually-loaded Company row -- a missing/deleted target must
        # never fabricate a match.
        forced_target_exists = forced_id is not None and forced_id in self._known_ids
        if forced_target_exists:
            candidate_ids.add(forced_id)

        if not candidate_ids:
            return OUTCOME_UNMATCHED, None

        used_alias = False
        collapsed: set[int] = set()
        for candidate_id in candidate_ids:
            canonical_id = self._alias_to_canonical.get(candidate_id)
            if canonical_id is not None:
                used_alias = True
                collapsed.add(canonical_id)
            else:
                collapsed.add(candidate_id)

        if len(collapsed) > 1:
            return OUTCOME_AMBIGUOUS, None

        resolved_id = next(iter(collapsed))
        if forced_target_exists and resolved_id == forced_id:
            method = METHOD_FORCED_OVERRIDE
        elif used_alias:
            method = METHOD_ALIAS_COLLAPSED
        else:
            method = METHOD_DIRECT_KEY
        return OUTCOME_MATCHED, method


def _safe_error_type(exc: Exception) -> str:
    """Fail-closed exception-class-name sanitization for ``error_counts``
    keys. Only an identifier-shaped name (``[A-Za-z_][A-Za-z0-9_]*`` --
    i.e. a real Python class name) is ever used verbatim; anything else
    (which should never happen for a real exception's ``__name__``, but
    is not something this module will ever trust blindly) is replaced
    with the fixed ``UNCLASSIFIED_ERROR_TYPE`` value so no free-text or
    dynamically-constructed class name can leak into the artifact."""
    name = type(exc).__name__
    if _ERROR_TYPE_RE.match(name):
        return name
    return UNCLASSIFIED_ERROR_TYPE


def compute_examined_digest(permit_ids: list[int]) -> str:
    """Full SHA-256 hex digest over the sorted, de-duplicated set of
    examined ``Permit.id`` values. Proves *that* a particular row-set was
    examined without ever serializing the set itself -- the id list is
    hashed here and never returned or retained anywhere else."""
    ordered = sorted(set(permit_ids))
    blob = ",".join(str(permit_id) for permit_id in ordered)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalization_bucket(raw_applicant: str) -> tuple[str, str | None, str | None]:
    """Returns ``(bucket, safe_organization, confidence_tier)``.
    ``safe_organization``/``confidence_tier`` are only non-``None`` when
    ``bucket == BUCKET_NORMALIZED_SAFE``.

    Only the four known ``normalize_surrey_applicant`` statuses are
    accepted explicitly. Any other status is a contract violation in this
    module's assumptions about that function -- not a per-row data
    problem -- and raises ``SurreyResolutionAuditError`` rather than being
    silently folded into ``normalized_safe``/``business_address`` tier by
    a permissive fallback."""
    result = normalize_surrey_applicant(raw_applicant)
    if result.status == STATUS_MISSING:
        return BUCKET_APPLICANT_MISSING, None, None
    if result.status == STATUS_UNRESOLVED:
        return BUCKET_NORMALIZATION_UNRESOLVED, None, None
    if result.status == STATUS_NORMALIZED_LEGAL_SUFFIX:
        return BUCKET_NORMALIZED_SAFE, result.organization, TIER_HIGH
    if result.status == STATUS_NORMALIZED_BUSINESS_ADDRESS:
        return BUCKET_NORMALIZED_SAFE, result.organization, TIER_MEDIUM
    raise SurreyResolutionAuditError(
        f"unrecognized normalize_surrey_applicant status: {result.status!r}"
    )


def _validate_sample_size(sample_size: int | None) -> None:
    """Fail-closed argument validation -- called before ``session`` is
    touched at all. ``bool`` is deliberately rejected even though
    ``isinstance(True, int)`` is true in Python: only ``type(value) is
    int`` is accepted, so ``True``/``False`` (and any other ``int``
    subclass) raise the same as a float, string, or negative number."""
    if sample_size is None:
        return
    if type(sample_size) is not int or sample_size < 0:
        raise SurreyResolutionAuditError(
            f"sample_size must be a non-negative int or None, got {sample_size!r}"
        )


def audit_surrey_permit_resolution(
    session: Session,
    *,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """Single read-only pass over ``source='surrey'`` permits, deterministically
    ordered by ``Permit.id`` ascending (``LIMIT sample_size`` when given).

    Never issues a write. Never returns a permit id, company id, applicant
    string, company name, address, or raw exception message -- only
    aggregate counts, histograms, and a content digest over the examined
    permit id set.

    Normalization and resolution errors are counted, and bucketed by
    ``{stage}:{error_type}``, separately -- ``counts["normalization_errors"]``
    plus every normalization bucket always equals ``counts["total"]``;
    ``counts["resolution_errors"]`` plus every resolution outcome always
    equals ``counts["normalized_safe"]``; ``counts["errors"]`` always equals
    their sum. Any of these invariants failing, or an unrecognized
    normalization bucket / resolution outcome / match method / confidence
    tier appearing (a contract violation in this module itself, not a
    per-row data problem), raises ``SurreyResolutionAuditError`` immediately
    rather than ever returning an inconsistent or silently-incomplete
    aggregate.
    """
    _validate_sample_size(sample_size)

    index = CompanyIndex(session)

    query = (
        select(Permit.id, Permit.applicant)
        .where(Permit.source == "surrey")
        .order_by(Permit.id)
    )
    if sample_size is not None:
        query = query.limit(sample_size)

    counts: dict[str, int] = {
        "total": 0,
        BUCKET_APPLICANT_MISSING: 0,
        BUCKET_NORMALIZED_SAFE: 0,
        BUCKET_NORMALIZATION_UNRESOLVED: 0,
        "normalization_errors": 0,
        "matched_existing_company": 0,
        "ambiguous_existing_company": 0,
        "unmatched_existing_company": 0,
        "duplicate_risk": 0,
        "resolution_errors": 0,
        "errors": 0,
    }
    match_method_histogram: dict[str, int] = {
        METHOD_DIRECT_KEY: 0,
        METHOD_ALIAS_COLLAPSED: 0,
        METHOD_FORCED_OVERRIDE: 0,
    }
    confidence_tier_histogram: dict[str, int] = {
        TIER_HIGH: 0,
        TIER_MEDIUM: 0,
    }
    error_counts: dict[str, int] = {}
    examined_ids: list[int] = []

    for permit_id, raw_applicant in session.execute(query).all():
        examined_ids.append(int(permit_id))
        counts["total"] += 1

        try:
            bucket, organization, tier = _normalization_bucket(raw_applicant or "")
        except SurreyResolutionAuditError:
            # A contract violation in this module's own assumptions (an
            # unrecognized normalize_surrey_applicant status) -- must
            # interrupt the whole audit, never be folded into a per-row
            # normalization_error.
            raise
        except Exception as exc:
            counts["normalization_errors"] += 1
            counts["errors"] += 1
            key = f"{STAGE_NORMALIZATION}:{_safe_error_type(exc)}"
            error_counts[key] = error_counts.get(key, 0) + 1
            continue

        if bucket not in (
            BUCKET_APPLICANT_MISSING,
            BUCKET_NORMALIZED_SAFE,
            BUCKET_NORMALIZATION_UNRESOLVED,
        ):
            raise SurreyResolutionAuditError(
                f"unrecognized normalization bucket: {bucket!r}"
            )
        counts[bucket] += 1
        if bucket != BUCKET_NORMALIZED_SAFE:
            continue

        if tier not in confidence_tier_histogram:
            raise SurreyResolutionAuditError(f"unrecognized confidence tier: {tier!r}")
        confidence_tier_histogram[tier] += 1

        try:
            outcome, method = index.match(organization or "")
        except SurreyResolutionAuditError:
            raise
        except Exception as exc:
            counts["resolution_errors"] += 1
            counts["errors"] += 1
            key = f"{STAGE_RESOLUTION}:{_safe_error_type(exc)}"
            error_counts[key] = error_counts.get(key, 0) + 1
            continue

        if outcome == OUTCOME_MATCHED:
            counts["matched_existing_company"] += 1
            if method not in match_method_histogram:
                raise SurreyResolutionAuditError(
                    f"unrecognized match method: {method!r}"
                )
            match_method_histogram[method] += 1
        elif outcome == OUTCOME_AMBIGUOUS:
            counts["ambiguous_existing_company"] += 1
            # duplicate_risk deliberately mirrors ambiguous_existing_company
            # exactly -- it is the same signal (a safely-normalized
            # organization whose canonical key resolves to more than one
            # distinct existing Company identity), surfaced under its own
            # name for a consumer reading only the top-level risk counts.
            counts["duplicate_risk"] += 1
        elif outcome == OUTCOME_UNMATCHED:
            counts["unmatched_existing_company"] += 1
        else:
            raise SurreyResolutionAuditError(
                f"unrecognized resolution outcome: {outcome!r}"
            )

    normalization_total = (
        counts[BUCKET_APPLICANT_MISSING]
        + counts[BUCKET_NORMALIZED_SAFE]
        + counts[BUCKET_NORMALIZATION_UNRESOLVED]
    )
    if normalization_total + counts["normalization_errors"] != counts["total"]:
        raise SurreyResolutionAuditError(
            "normalization bucket counts do not sum to total examined rows"
        )

    resolution_total = (
        counts["matched_existing_company"]
        + counts["ambiguous_existing_company"]
        + counts["unmatched_existing_company"]
    )
    if resolution_total + counts["resolution_errors"] != counts[BUCKET_NORMALIZED_SAFE]:
        raise SurreyResolutionAuditError(
            "resolution bucket counts do not sum to normalized_safe rows"
        )

    if counts["errors"] != counts["normalization_errors"] + counts["resolution_errors"]:
        raise SurreyResolutionAuditError(
            "errors does not equal the sum of normalization_errors and resolution_errors"
        )

    return {
        "counts": counts,
        "match_method_histogram": match_method_histogram,
        "confidence_tier_histogram": confidence_tier_histogram,
        "error_counts": error_counts,
        "examined_count": len(examined_ids),
        "examined_ids_digest": compute_examined_digest(examined_ids),
    }
