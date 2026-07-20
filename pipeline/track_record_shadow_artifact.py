"""Aggregate-only artifact builder for the company track-record shadow
dry-run (PR-G3.3a).

Takes the return value of ``backfill_company_track_records(...,
dry_run=True)`` (PR-G3.2, with the additive ``coverage`` field on each
``results[*]`` entry added alongside this module) and reduces it to an
artifact containing only counts, histograms, and a single set-identity
digest -- never a raw company id, name, address, phone, AI summary text,
per-company result, exception message, or connection detail.

The eligibility digest (``compute_eligibility_digest``) proves *that* a
particular set of company ids was selected, without ever serializing the
set itself: the ids are hashed and immediately discarded by every caller
in this module -- ``build_shadow_dryrun_artifact`` never retains or
returns the id list it derives internally.

Fail-closed reconstruction of the selected set
------------------------------------------------
The eligibility digest must cover the *exact* selected batch -- including
companies that failed during adapter/scorer/assignment/commit, as long as
their identity was known (``errors[*]["company_id"]`` is not ``None``).
``_collect_selected_company_ids`` reconstructs this set as the union of
every ``results[*]["company_id"]`` (adapter+scorer succeeded, regardless
of what happened afterward) and every ``errors[*]["company_id"]`` that is
not ``None``, then validates it fail-closed before it is ever hashed:

  - a ``None`` ``company_id`` in ``errors`` (an identity-stage failure --
    ``STAGE_IDENTITY``) means the true selected set can never be fully
    reconstructed -- ``TrackRecordShadowArtifactError`` is raised and no
    artifact is built, rather than silently hashing an undercounted set;
  - a duplicate id, an id of the wrong type, or a recovered-id count that
    does not equal ``backfill_result["selected"]`` all raise the same way.

Every error entry is also validated against the fixed, closed
``STAGE_*`` set (imported from ``pipeline.track_record_backfill``, the
single source of truth) and an exception-class-name-shaped
``error_type`` -- an unrecognized stage or a free-text-shaped
``error_type`` raises rather than being serialized into the artifact.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from pipeline.track_record_backfill import (
    STAGE_ADAPTER,
    STAGE_ASSIGNMENT,
    STAGE_COMMIT,
    STAGE_IDENTITY,
    STAGE_SCORER,
)

ARTIFACT_SCHEMA_VERSION = 1

# Fixed, closed set of score buckets -- deciles, plus a top bucket that
# also captures the maximum possible score (100).
SCORE_HISTOGRAM_BUCKETS: tuple[str, ...] = (
    "0-9",
    "10-19",
    "20-29",
    "30-39",
    "40-49",
    "50-59",
    "60-69",
    "70-79",
    "80-89",
    "90-100",
)

# Fixed, closed set of TrackRecordCoverage boolean fields this module
# aggregates. Mirrors pipeline.scoring.company_track_record.TrackRecordCoverage
# exactly; never derived dynamically from an arbitrary dict's keys.
COVERAGE_BOOLEAN_FIELDS: tuple[str, ...] = (
    "core_evidence_present",
    "has_permit_evidence",
    "has_award_evidence",
    "has_recency_signal",
    "has_google_signal",
    "has_buyer_diversity_signal",
)

# Fixed, closed set of per-company failure stages -- the single source of
# truth is pipeline.track_record_backfill's own STAGE_* constants.
_KNOWN_STAGES = frozenset(
    {STAGE_IDENTITY, STAGE_ADAPTER, STAGE_SCORER, STAGE_ASSIGNMENT, STAGE_COMMIT}
)

# A Python exception class name: letters/digits/underscore, not starting
# with a digit. Deliberately strict -- rejects anything with spaces,
# punctuation, or other free-text shape, which would indicate a raw
# message leaked in instead of a genuine class name.
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "SCORE_HISTOGRAM_BUCKETS",
    "COVERAGE_BOOLEAN_FIELDS",
    "TrackRecordShadowArtifactError",
    "compute_eligibility_digest",
    "build_score_histogram",
    "aggregate_coverage",
    "aggregate_errors",
    "build_shadow_dryrun_artifact",
]


class TrackRecordShadowArtifactError(ValueError):
    """Raised by ``build_shadow_dryrun_artifact`` (or a helper it calls)
    when the underlying ``backfill_company_track_records`` result cannot
    be reduced to a fail-closed, accurate artifact -- an identity-stage
    failure (``company_id=None``), a duplicate or wrong-typed id, a
    recovered-id count that doesn't match ``selected``, or an error entry
    with an unrecognized stage / non-identifier-shaped ``error_type``.
    Raised instead of silently building an incomplete, under-counted, or
    unsafe artifact -- the caller must never write an artifact when this
    is raised."""


def _is_plain_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int)


def _validate_error_entry(error: dict[str, Any]) -> None:
    """Fail-closed validation of a single ``errors[*]`` entry -- company_id
    is either ``None`` or a plain int, ``stage`` is one of the fixed
    ``STAGE_*`` values, and ``error_type`` is identifier-shaped (a real
    exception class name, never free text)."""
    if not isinstance(error, dict):
        raise TrackRecordShadowArtifactError(f"error entry is not a dict: {error!r}")

    company_id = error.get("company_id")
    if company_id is not None and not _is_plain_int(company_id):
        raise TrackRecordShadowArtifactError(
            f"error entry company_id must be None or a plain int, got {company_id!r}"
        )

    stage = error.get("stage")
    if stage not in _KNOWN_STAGES:
        raise TrackRecordShadowArtifactError(
            f"error entry has an unrecognized stage: {stage!r}"
        )

    error_type = error.get("error_type")
    if not isinstance(error_type, str) or not _ERROR_TYPE_RE.match(error_type):
        raise TrackRecordShadowArtifactError(
            f"error entry has a non-identifier-shaped error_type: {error_type!r}"
        )


def _collect_selected_company_ids(backfill_result: dict[str, Any]) -> list[int]:
    """Fail-closed reconstruction of the exact selected id set -- see the
    module docstring's "Fail-closed reconstruction" section for the full
    contract. Raises ``TrackRecordShadowArtifactError`` (building
    nothing) rather than ever returning an incomplete or inconsistent
    set."""
    ids: list[int] = []

    for entry in backfill_result["results"]:
        company_id = entry.get("company_id")
        if not _is_plain_int(company_id):
            raise TrackRecordShadowArtifactError(
                f"results entry company_id must be a plain int, got {company_id!r}"
            )
        ids.append(company_id)

    for error in backfill_result["errors"]:
        _validate_error_entry(error)
        company_id = error.get("company_id")
        if company_id is None:
            raise TrackRecordShadowArtifactError(
                "cannot build a fail-closed eligibility digest: at least one "
                "company failed before its identity could be read "
                f"(stage={error.get('stage')!r}) -- refusing to build an "
                "artifact from an incomplete/undercounted selected set"
            )
        ids.append(company_id)

    if len(set(ids)) != len(ids):
        raise TrackRecordShadowArtifactError(
            "recovered company ids contain duplicates -- refusing to build "
            "an artifact from an inconsistent result"
        )

    expected = backfill_result["selected"]
    if len(ids) != expected:
        raise TrackRecordShadowArtifactError(
            f"recovered {len(ids)} company id(s) but "
            f"backfill_result['selected']={expected!r} -- refusing to build "
            "an artifact from a mismatched result"
        )

    return ids


def compute_eligibility_digest(company_ids: list[int]) -> str:
    """Full SHA-256 hex digest (64 hex characters) over the
    deterministically ordered, de-duplicated (set-normalized) selected
    company-id set.

    Fail-closed: every value must be a plain, non-bool int -- anything
    else raises ``TrackRecordShadowArtifactError`` rather than being
    silently coerced or skipped.

    The id list itself is hashed here and never returned, retained, or
    serialized anywhere else in this module -- only this digest, which
    changes if a single id is added, removed, or substituted in the set,
    proves set-identity between two runs without ever revealing the
    set's contents."""
    unique_ids: set[int] = set()
    for company_id in company_ids:
        if not _is_plain_int(company_id):
            raise TrackRecordShadowArtifactError(
                f"eligibility digest input contains a non-int id: {company_id!r}"
            )
        unique_ids.add(company_id)
    ordered = sorted(unique_ids)
    blob = ",".join(str(company_id) for company_id in ordered)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _score_bucket(score: int) -> str:
    if score >= 100:
        return "90-100"
    if score < 0:
        return "0-9"
    return SCORE_HISTOGRAM_BUCKETS[score // 10]


def build_score_histogram(scores: list[int | None]) -> dict[str, int]:
    """Counts of non-``None`` scores per fixed decile bucket. ``None``
    scores are not counted here -- see the separate ``score_null_count``
    field in the artifact, kept distinct per the artifact contract."""
    histogram = {bucket: 0 for bucket in SCORE_HISTOGRAM_BUCKETS}
    for score in scores:
        if score is None:
            continue
        histogram[_score_bucket(score)] += 1
    return histogram


def aggregate_coverage(coverage_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts of ``True`` per ``TrackRecordCoverage`` boolean field across
    the batch, plus a histogram of ``bonus_factors_present`` (0, 1, or 2).
    Never retains which specific company contributed which value."""
    counts = {field: 0 for field in COVERAGE_BOOLEAN_FIELDS}
    bonus_histogram = {0: 0, 1: 0, 2: 0}
    for coverage in coverage_dicts:
        for field in COVERAGE_BOOLEAN_FIELDS:
            if coverage.get(field):
                counts[field] += 1
        bonus_count = coverage.get("bonus_factors_present", 0)
        if bonus_count in bonus_histogram:
            bonus_histogram[bonus_count] += 1
    return {
        "counts": counts,
        "bonus_factors_present_histogram": {
            str(key): value for key, value in bonus_histogram.items()
        },
    }


def aggregate_errors(errors: list[dict[str, Any]]) -> dict[str, int]:
    """Counts errors by a fixed ``"{stage}:{error_type}"`` key -- never
    includes ``company_id`` or any other per-company detail. Fail-closed:
    every entry is validated via ``_validate_error_entry`` first --
    ``stage`` must be one of the fixed ``STAGE_*`` constants and
    ``error_type`` must be identifier-shaped (a real exception class
    name); an unrecognized stage or a free-text-shaped ``error_type``
    raises ``TrackRecordShadowArtifactError`` rather than being
    serialized."""
    counts: dict[str, int] = {}
    for error in errors:
        _validate_error_entry(error)
        key = f"{error['stage']}:{error['error_type']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_shadow_dryrun_artifact(
    backfill_result: dict[str, Any],
    *,
    git_commit_sha: str,
    sample_size: int | None,
    explicit_company_ids: list[int] | None,
    force: bool,
    generated_at: datetime,
) -> dict[str, Any]:
    """Reduce a ``backfill_company_track_records(..., dry_run=True)``
    result to the aggregate-only shadow dry-run artifact contract.

    Raises ``TrackRecordShadowArtifactError`` -- before building anything
    -- if the exact selected id set cannot be fail-closed reconstructed
    (see ``_collect_selected_company_ids``) or if any error entry fails
    validation (see ``_validate_error_entry``). The returned artifact
    dict never contains a per-company list, nor any individual id, name,
    address, phone, AI summary text, or raw exception message.
    """
    selected_ids = _collect_selected_company_ids(backfill_result)

    results = backfill_result["results"]
    scores = [entry.get("score") for entry in results]
    coverage_dicts = [entry["coverage"] for entry in results if "coverage" in entry]

    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "dry_run": True,
        "git_commit_sha": git_commit_sha,
        "algorithm_version": backfill_result["algorithm_version"],
        "scope": {
            "sample_size": sample_size,
            "explicit_company_ids_provided": explicit_company_ids is not None,
            "explicit_company_ids_count": (
                len(explicit_company_ids) if explicit_company_ids is not None else None
            ),
            "force": force,
        },
        "selected": backfill_result["selected"],
        "processed": backfill_result["processed"],
        "skipped": backfill_result["skipped"],
        "failed": backfill_result["failed"],
        "score_histogram": build_score_histogram(scores),
        "score_null_count": sum(1 for score in scores if score is None),
        "coverage": aggregate_coverage(coverage_dicts),
        "diagnostics_notes_count": backfill_result["diagnostics_notes_count"],
        "error_counts": aggregate_errors(backfill_result["errors"]),
        "eligibility_digest": compute_eligibility_digest(selected_ids),
        "reference_date": backfill_result["reference_date"],
        "computed_at": backfill_result["computed_at"],
        "generated_at": generated_at.isoformat(),
    }
