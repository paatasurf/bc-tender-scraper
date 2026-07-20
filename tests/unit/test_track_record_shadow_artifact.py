"""Tests for pipeline/track_record_shadow_artifact.py (PR-G3.3a).

Pure functions -- no DB, no session, no network anywhere in this file.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from pipeline.track_record_backfill import (
    STAGE_ADAPTER,
    STAGE_ASSIGNMENT,
    STAGE_COMMIT,
    STAGE_IDENTITY,
    STAGE_SCORER,
)
from pipeline.track_record_shadow_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    COVERAGE_BOOLEAN_FIELDS,
    SCORE_HISTOGRAM_BUCKETS,
    TrackRecordShadowArtifactError,
    aggregate_coverage,
    aggregate_errors,
    build_score_histogram,
    build_shadow_dryrun_artifact,
    compute_eligibility_digest,
)

GENERATED_AT = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


# ===================================================================
# compute_eligibility_digest
# ===================================================================


def test_eligibility_digest_is_full_64_char_sha256_hex():
    digest = compute_eligibility_digest([3, 1, 2])
    assert len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)


def test_eligibility_digest_stable_regardless_of_input_order():
    assert compute_eligibility_digest([1, 2, 3]) == compute_eligibility_digest(
        [3, 2, 1]
    )


def test_eligibility_digest_matches_manual_sha256_of_sorted_ids():
    ids = [42, 7, 100]
    expected = hashlib.sha256("7,42,100".encode("utf-8")).hexdigest()
    assert compute_eligibility_digest(ids) == expected


def test_eligibility_digest_changes_when_set_changes():
    base = compute_eligibility_digest([1, 2, 3])
    assert compute_eligibility_digest([1, 2, 4]) != base
    assert compute_eligibility_digest([1, 2]) != base
    assert compute_eligibility_digest([1, 2, 3, 4]) != base


def test_eligibility_digest_of_empty_set_is_deterministic():
    assert compute_eligibility_digest([]) == compute_eligibility_digest([])
    assert compute_eligibility_digest([]) == hashlib.sha256(b"").hexdigest()


def test_eligibility_digest_normalizes_via_unique_set():
    """A duplicated id in the input is set-normalized away -- the digest
    reflects the unique set, not the raw (possibly repeated) input list."""
    assert compute_eligibility_digest([1, 2, 2, 3]) == compute_eligibility_digest(
        [1, 2, 3]
    )


@pytest.mark.parametrize("bad_id", [True, False, "5", 5.0, None, [5], {5}])
def test_eligibility_digest_rejects_non_int_ids(bad_id):
    with pytest.raises(TrackRecordShadowArtifactError):
        compute_eligibility_digest([1, bad_id])


# ===================================================================
# build_score_histogram
# ===================================================================


def test_score_histogram_buckets_are_fixed_deciles():
    assert SCORE_HISTOGRAM_BUCKETS == (
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


@pytest.mark.parametrize(
    "score,bucket",
    [
        (0, "0-9"),
        (9, "0-9"),
        (10, "10-19"),
        (55, "50-59"),
        (89, "80-89"),
        (90, "90-100"),
        (100, "90-100"),
    ],
)
def test_score_histogram_places_score_in_correct_bucket(score, bucket):
    histogram = build_score_histogram([score])
    assert histogram[bucket] == 1
    assert sum(histogram.values()) == 1


def test_score_histogram_ignores_none_scores():
    histogram = build_score_histogram([None, None, 50])
    assert sum(histogram.values()) == 1
    assert histogram["50-59"] == 1


def test_score_histogram_arithmetic_sums_to_non_null_count():
    scores = [0, 5, 15, 50, 99, 100, None, None]
    histogram = build_score_histogram(scores)
    non_null = sum(1 for s in scores if s is not None)
    assert sum(histogram.values()) == non_null


def test_score_histogram_all_buckets_present_even_when_empty():
    histogram = build_score_histogram([])
    assert set(histogram.keys()) == set(SCORE_HISTOGRAM_BUCKETS)
    assert sum(histogram.values()) == 0


# ===================================================================
# aggregate_coverage
# ===================================================================


def test_aggregate_coverage_counts_true_fields():
    coverage_dicts = [
        {
            "core_evidence_present": True,
            "has_permit_evidence": True,
            "has_award_evidence": False,
            "has_recency_signal": True,
            "has_google_signal": False,
            "has_buyer_diversity_signal": False,
            "bonus_factors_present": 0,
        },
        {
            "core_evidence_present": True,
            "has_permit_evidence": False,
            "has_award_evidence": True,
            "has_recency_signal": True,
            "has_google_signal": True,
            "has_buyer_diversity_signal": True,
            "bonus_factors_present": 2,
        },
    ]
    result = aggregate_coverage(coverage_dicts)
    assert result["counts"]["core_evidence_present"] == 2
    assert result["counts"]["has_permit_evidence"] == 1
    assert result["counts"]["has_award_evidence"] == 1
    assert result["counts"]["has_google_signal"] == 1
    assert result["bonus_factors_present_histogram"] == {"0": 1, "1": 0, "2": 1}


def test_aggregate_coverage_all_fields_present_for_empty_input():
    result = aggregate_coverage([])
    assert set(result["counts"].keys()) == set(COVERAGE_BOOLEAN_FIELDS)
    assert all(v == 0 for v in result["counts"].values())
    assert result["bonus_factors_present_histogram"] == {"0": 0, "1": 0, "2": 0}


def test_aggregate_coverage_never_leaks_extra_dict_keys():
    """A coverage dict with an unexpected extra key (simulating a future
    scorer field, or -- adversarially -- an injected payload) must never
    show up in the aggregate output; only the fixed COVERAGE_BOOLEAN_FIELDS
    set is ever read."""
    coverage_dicts = [
        {field: True for field in COVERAGE_BOOLEAN_FIELDS}
        | {"bonus_factors_present": 1, "injected_company_name": "Acme Corp"}
    ]
    result = aggregate_coverage(coverage_dicts)
    serialized = repr(result)
    assert "Acme Corp" not in serialized
    assert "injected_company_name" not in serialized


# ===================================================================
# aggregate_errors
# ===================================================================


def test_aggregate_errors_counts_by_stage_and_type():
    errors = [
        {
            "company_id": 1,
            "stage": "adapter",
            "error_type": "CompanyTrackRecordAdapterError",
        },
        {
            "company_id": 2,
            "stage": "adapter",
            "error_type": "CompanyTrackRecordAdapterError",
        },
        {"company_id": 3, "stage": "commit", "error_type": "RuntimeError"},
    ]
    counts = aggregate_errors(errors)
    assert counts == {
        "adapter:CompanyTrackRecordAdapterError": 2,
        "commit:RuntimeError": 1,
    }


def test_aggregate_errors_never_includes_company_id():
    errors = [{"company_id": 12345, "stage": "scorer", "error_type": "ValueError"}]
    counts = aggregate_errors(errors)
    assert "12345" not in repr(counts)


def test_aggregate_errors_empty_input_yields_empty_dict():
    assert aggregate_errors([]) == {}


def test_aggregate_errors_accepts_every_known_stage():
    for stage in (
        STAGE_IDENTITY,
        STAGE_ADAPTER,
        STAGE_SCORER,
        STAGE_ASSIGNMENT,
        STAGE_COMMIT,
    ):
        counts = aggregate_errors(
            [{"company_id": 1, "stage": stage, "error_type": "ValueError"}]
        )
        assert counts == {f"{stage}:ValueError": 1}


@pytest.mark.parametrize(
    "bad_stage", ["DROP TABLE companies", "adapters", "", None, 5, "Adapter"]
)
def test_aggregate_errors_rejects_unrecognized_stage(bad_stage):
    with pytest.raises(TrackRecordShadowArtifactError):
        aggregate_errors(
            [{"company_id": 1, "stage": bad_stage, "error_type": "ValueError"}]
        )


@pytest.mark.parametrize(
    "bad_error_type",
    [
        "RuntimeError; import os",
        "Runtime Error",
        "'; DROP TABLE companies; --",
        "",
        None,
        5,
        "postgresql://u:p@h/db",
    ],
)
def test_aggregate_errors_rejects_free_text_error_type(bad_error_type):
    with pytest.raises(TrackRecordShadowArtifactError):
        aggregate_errors(
            [{"company_id": 1, "stage": STAGE_ADAPTER, "error_type": bad_error_type}]
        )


def test_aggregate_errors_rejects_non_int_company_id():
    with pytest.raises(TrackRecordShadowArtifactError):
        aggregate_errors(
            [{"company_id": "1", "stage": STAGE_ADAPTER, "error_type": "ValueError"}]
        )


# ===================================================================
# build_shadow_dryrun_artifact -- full contract + leak-scan
# ===================================================================


def _backfill_result(**overrides):
    base = {
        "selected": 2,
        "processed": 2,
        "persisted": 0,
        "skipped": 2,
        "failed": 0,
        "dry_run": True,
        "algorithm_version": "company_track_record_v1",
        "reference_date": "2026-07-20",
        "computed_at": "2026-07-20T12:00:00+00:00",
        "diagnostics_notes_count": 1,
        "errors": [],
        "results": [
            {
                "company_id": 101,
                "score": 55,
                "status": "dry_run_computed",
                "diagnostics_notes": 1,
                "coverage": {
                    "core_evidence_present": True,
                    "has_permit_evidence": True,
                    "has_award_evidence": False,
                    "has_recency_signal": True,
                    "has_google_signal": False,
                    "has_buyer_diversity_signal": False,
                    "bonus_factors_present": 0,
                },
            },
            {
                "company_id": 202,
                "score": None,
                "status": "dry_run_computed",
                "diagnostics_notes": 0,
                "coverage": {
                    "core_evidence_present": False,
                    "has_permit_evidence": False,
                    "has_award_evidence": False,
                    "has_recency_signal": False,
                    "has_google_signal": False,
                    "has_buyer_diversity_signal": False,
                    "bonus_factors_present": 0,
                },
            },
        ],
    }
    base.update(overrides)
    return base


# ===================================================================
# Fail-closed selected-set reconstruction
# ===================================================================


def test_failed_company_with_known_id_still_included_in_eligibility_digest():
    """An adapter/scorer/assignment/commit failure (identity known) must
    still contribute its id to the eligibility digest -- the digest
    covers the exact selected batch, not just the companies that
    succeeded."""
    result = _backfill_result(
        selected=2,
        processed=1,
        skipped=1,
        failed=1,
        results=[
            {
                "company_id": 101,
                "score": 55,
                "status": "dry_run_computed",
                "diagnostics_notes": 0,
                "coverage": {field: False for field in COVERAGE_BOOLEAN_FIELDS}
                | {"bonus_factors_present": 0},
            }
        ],
        errors=[
            {
                "company_id": 202,
                "stage": STAGE_ADAPTER,
                "error_type": "CompanyTrackRecordAdapterError",
            }
        ],
    )
    artifact = build_shadow_dryrun_artifact(
        result,
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    assert artifact["eligibility_digest"] == compute_eligibility_digest([101, 202])


def test_identity_stage_failure_with_none_id_refuses_artifact():
    result = _backfill_result(
        selected=2,
        processed=1,
        skipped=1,
        failed=1,
        errors=[
            {"company_id": None, "stage": STAGE_IDENTITY, "error_type": "RuntimeError"}
        ],
    )
    with pytest.raises(TrackRecordShadowArtifactError):
        build_shadow_dryrun_artifact(
            result,
            git_commit_sha="abc123",
            sample_size=None,
            explicit_company_ids=None,
            force=False,
            generated_at=GENERATED_AT,
        )


def test_selected_mismatch_refuses_artifact():
    result = _backfill_result(selected=5)  # only 2 recoverable ids in results
    with pytest.raises(TrackRecordShadowArtifactError):
        build_shadow_dryrun_artifact(
            result,
            git_commit_sha="abc123",
            sample_size=None,
            explicit_company_ids=None,
            force=False,
            generated_at=GENERATED_AT,
        )


def test_duplicate_id_across_results_and_errors_refuses_artifact():
    result = _backfill_result(
        selected=2,
        errors=[
            {
                "company_id": 101,  # same id as a results entry
                "stage": STAGE_COMMIT,
                "error_type": "RuntimeError",
            }
        ],
    )
    with pytest.raises(TrackRecordShadowArtifactError):
        build_shadow_dryrun_artifact(
            result,
            git_commit_sha="abc123",
            sample_size=None,
            explicit_company_ids=None,
            force=False,
            generated_at=GENERATED_AT,
        )


@pytest.mark.parametrize("bad_id", [True, "101", 101.0])
def test_invalid_result_company_id_type_refuses_artifact(bad_id):
    result = _backfill_result()
    result["results"][0]["company_id"] = bad_id
    with pytest.raises(TrackRecordShadowArtifactError):
        build_shadow_dryrun_artifact(
            result,
            git_commit_sha="abc123",
            sample_size=None,
            explicit_company_ids=None,
            force=False,
            generated_at=GENERATED_AT,
        )


def test_malicious_stage_refuses_artifact_and_never_serializes():
    result = _backfill_result(
        selected=2,
        errors=[
            {
                "company_id": 303,
                "stage": "DROP TABLE companies; --",
                "error_type": "RuntimeError",
            }
        ],
    )
    with pytest.raises(TrackRecordShadowArtifactError) as excinfo:
        build_shadow_dryrun_artifact(
            result,
            git_commit_sha="abc123",
            sample_size=None,
            explicit_company_ids=None,
            force=False,
            generated_at=GENERATED_AT,
        )
    # The error message itself is developer-facing (raised, not returned
    # to a caller that serializes it) -- but confirm no artifact-shaped
    # dict is ever produced regardless of what the exception says.
    assert excinfo.type is TrackRecordShadowArtifactError


def test_malicious_error_type_refuses_artifact_and_never_serializes():
    result = _backfill_result(
        selected=2,
        errors=[
            {
                "company_id": 303,
                "stage": STAGE_COMMIT,
                "error_type": "'; DROP TABLE companies; --",
            }
        ],
    )
    with pytest.raises(TrackRecordShadowArtifactError):
        build_shadow_dryrun_artifact(
            result,
            git_commit_sha="abc123",
            sample_size=None,
            explicit_company_ids=None,
            force=False,
            generated_at=GENERATED_AT,
        )


def test_artifact_contains_exactly_the_contracted_fields():
    artifact = build_shadow_dryrun_artifact(
        _backfill_result(),
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    assert set(artifact.keys()) == {
        "artifact_schema_version",
        "dry_run",
        "git_commit_sha",
        "algorithm_version",
        "scope",
        "selected",
        "processed",
        "skipped",
        "failed",
        "score_histogram",
        "score_null_count",
        "coverage",
        "diagnostics_notes_count",
        "error_counts",
        "eligibility_digest",
        "reference_date",
        "computed_at",
        "generated_at",
    }
    assert artifact["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert artifact["dry_run"] is True
    assert artifact["git_commit_sha"] == "abc123"
    assert artifact["algorithm_version"] == "company_track_record_v1"
    assert artifact["selected"] == 2
    assert artifact["score_null_count"] == 1
    assert len(artifact["eligibility_digest"]) == 64


def test_artifact_scope_reports_count_not_explicit_ids():
    # Distinctive 6-digit ids -- won't accidentally collide with substrings
    # of the date/timestamp fields ("2026-07-20T12:00:00+00:00") that also
    # appear in the serialized artifact.
    artifact = build_shadow_dryrun_artifact(
        _backfill_result(),
        git_commit_sha="abc123",
        sample_size=5,
        explicit_company_ids=[900011, 900022, 900033],
        force=True,
        generated_at=GENERATED_AT,
    )
    assert artifact["scope"] == {
        "sample_size": 5,
        "explicit_company_ids_provided": True,
        "explicit_company_ids_count": 3,
        "force": True,
    }
    serialized = repr(artifact)
    assert "900011" not in serialized
    assert "900022" not in serialized
    assert "900033" not in serialized


def test_artifact_scope_no_explicit_ids_reports_none_count():
    artifact = build_shadow_dryrun_artifact(
        _backfill_result(),
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    assert artifact["scope"]["explicit_company_ids_provided"] is False
    assert artifact["scope"]["explicit_company_ids_count"] is None


def test_artifact_never_contains_per_company_results_list():
    artifact = build_shadow_dryrun_artifact(
        _backfill_result(),
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    assert "results" not in artifact


def test_artifact_never_leaks_injected_ids_names_or_secrets():
    """Simulates a maximally adversarial backfill_result -- injected
    company ids, a company-name-shaped string, and a connection-URL/API-key
    -shaped string riding along in fields the artifact builder must never
    read verbatim -- and confirms none of it survives into the artifact."""
    poisoned = _backfill_result(
        selected=1,
        results=[
            {
                "company_id": 999999,
                "score": 70,
                "status": "dry_run_computed",
                "diagnostics_notes": 0,
                "coverage": {
                    "core_evidence_present": True,
                    "has_permit_evidence": True,
                    "has_award_evidence": True,
                    "has_recency_signal": True,
                    "has_google_signal": True,
                    "has_buyer_diversity_signal": True,
                    "bonus_factors_present": 2,
                    "injected_note": "Acme Construction Ltd, 555-0100, postgresql://u:p@h/db",
                },
            }
        ],
    )
    artifact = build_shadow_dryrun_artifact(
        poisoned,
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    serialized = repr(artifact)
    assert "999999" not in serialized
    assert "Acme Construction" not in serialized
    assert "555-0100" not in serialized
    assert "postgresql://" not in serialized


def test_artifact_eligibility_digest_matches_compute_eligibility_digest():
    result = _backfill_result()
    artifact = build_shadow_dryrun_artifact(
        result,
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    expected = compute_eligibility_digest([101, 202])
    assert artifact["eligibility_digest"] == expected


def test_artifact_generated_at_is_iso_and_matches_input():
    artifact = build_shadow_dryrun_artifact(
        _backfill_result(),
        git_commit_sha="abc123",
        sample_size=None,
        explicit_company_ids=None,
        force=False,
        generated_at=GENERATED_AT,
    )
    assert artifact["generated_at"] == GENERATED_AT.isoformat()
