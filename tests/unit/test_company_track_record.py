"""Unit tests for pipeline.scoring.company_track_record (PR-G1).

Fully deterministic -- no DB, no network, no AI/LLM calls, no wall-clock
reads. Every test supplies an explicit reference_date.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from pipeline.scoring.company_track_record import (
    AWARD_DEPTH_SATURATION_COUNT,
    BUYER_DIVERSITY_BONUS_MAX_POINTS,
    CORE_MAX_POINTS,
    COMPANY_TRACK_RECORD_ALGORITHM_VERSION,
    GOOGLE_BONUS_MAX_POINTS,
    LONGEVITY_MAX_POINTS,
    PERMIT_DEPTH_SATURATION_COUNT,
    RECENCY_DECAY_END_DAYS,
    RECENCY_FLOOR_POINTS,
    RECENCY_MAX_POINTS,
    RECENCY_PLATEAU_DAYS,
    TOTAL_MAX_POINTS,
    CompanyTrackRecordInput,
    InvalidCompanyTrackRecordInputError,
    score_company_track_record,
)

REF = date(2024, 7, 1)


def _factor(result, key):
    for f in result.breakdown:
        if f.factor == key:
            return f
    raise KeyError(key)


def _expect_invalid(*, reference_date=REF, **kwargs):
    with pytest.raises(InvalidCompanyTrackRecordInputError):
        score_company_track_record(
            CompanyTrackRecordInput(**kwargs), reference_date=reference_date
        )


# ===================================================================
# 1. Core-evidence gate
# ===================================================================


def test_no_core_evidence_returns_none_score():
    result = score_company_track_record(CompanyTrackRecordInput(), reference_date=REF)
    assert result.score is None
    assert result.breakdown == ()
    assert result.reasons == ()
    assert result.coverage.core_evidence_present is False
    assert result.coverage.bonus_factors_present == 0


def test_no_core_evidence_with_google_present_still_returns_none():
    """Bonus-only data (no permits/awards) is not core evidence -- but
    coverage must still honestly report that the Google signal exists."""
    result = score_company_track_record(
        CompanyTrackRecordInput(google_rating=4.8, google_reviews_count=100),
        reference_date=REF,
    )
    assert result.score is None
    assert result.breakdown == ()
    assert result.coverage.core_evidence_present is False
    assert result.coverage.has_google_signal is True
    assert result.coverage.bonus_factors_present == 1


def test_permit_only_has_core_evidence():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    assert result.score is not None
    assert result.coverage.core_evidence_present is True
    assert result.coverage.has_permit_evidence is True
    assert result.coverage.has_award_evidence is False


def test_award_only_has_core_evidence():
    result = score_company_track_record(
        CompanyTrackRecordInput(award_count=1, last_award_date=REF),
        reference_date=REF,
    )
    assert result.score is not None
    assert result.coverage.has_permit_evidence is False
    assert result.coverage.has_award_evidence is True


# ===================================================================
# 2. permit_depth / award_depth -- independent curves, no double counting
# ===================================================================


def test_permit_depth_log_scaled_values():
    cases = {1: 6, 3: 13, PERMIT_DEPTH_SATURATION_COUNT: 30, 30: 30, 1000: 30}
    for count, expected in cases.items():
        result = score_company_track_record(
            CompanyTrackRecordInput(total_projects=count, last_project_date=REF),
            reference_date=REF,
        )
        assert _factor(result, "permit_depth").points == expected, count


def test_award_depth_log_scaled_values():
    cases = {1: 4, 8: 14, AWARD_DEPTH_SATURATION_COUNT: 15, 1000: 15}
    for count, expected in cases.items():
        result = score_company_track_record(
            CompanyTrackRecordInput(award_count=count, last_award_date=REF),
            reference_date=REF,
        )
        assert _factor(result, "award_depth").points == expected, count


def test_permit_and_award_depth_are_independently_scored_not_summed():
    """The core anti-double-counting regression test.

    permit_depth for a permit-only company and award_depth for an
    award-only company must be numerically identical to the same-count
    factors inside a combined permit+award company -- proving neither
    stream's count leaks into the other's curve.
    """
    permit_only = score_company_track_record(
        CompanyTrackRecordInput(total_projects=8, last_project_date=REF),
        reference_date=REF,
    )
    award_only = score_company_track_record(
        CompanyTrackRecordInput(award_count=8, last_award_date=REF),
        reference_date=REF,
    )
    combined = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=8,
            last_project_date=REF,
            award_count=8,
            last_award_date=REF,
        ),
        reference_date=REF,
    )

    assert (
        _factor(combined, "permit_depth").points
        == _factor(permit_only, "permit_depth").points
    )
    assert (
        _factor(combined, "award_depth").points
        == _factor(award_only, "award_depth").points
    )
    # A naive "sum the two counts into one curve" implementation would NOT
    # satisfy the two equalities above -- a combined 16-count-equivalent
    # would saturate the shared curve higher than either 8-count alone does.


def test_permit_depth_absent_when_zero_projects():
    result = score_company_track_record(
        CompanyTrackRecordInput(award_count=1, last_award_date=REF),
        reference_date=REF,
    )
    factor = _factor(result, "permit_depth")
    assert factor.points == 0
    assert "No permit records" in factor.detail


def test_award_depth_absent_when_zero_awards():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    factor = _factor(result, "award_depth")
    assert factor.points == 0
    assert "No public award records" in factor.detail


# ===================================================================
# 3. Longevity -- single event vs multi-year history
# ===================================================================


def test_single_event_gives_zero_longevity_credit():
    """A single permit is not evidence of a multi-year history."""
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            first_project_date=date(2020, 3, 1),
            last_project_date=date(2020, 3, 1),
        ),
        reference_date=REF,
    )
    factor = _factor(result, "longevity")
    assert factor.points == 0
    assert "single event" in factor.detail


def test_two_records_same_day_gives_zero_span_credit():
    """Two records exist, but zero observed span between them."""
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=2,
            first_project_date=date(2020, 3, 1),
            last_project_date=date(2020, 3, 1),
        ),
        reference_date=REF,
    )
    factor = _factor(result, "longevity")
    assert factor.points == 0
    assert "No observed span" in factor.detail


def test_moderate_span_longevity_points():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=3,
            first_project_date=date(2018, 1, 1),
            last_project_date=date(2022, 1, 1),  # 1461 days == 4.0 years
        ),
        reference_date=REF,
    )
    assert _factor(result, "longevity").points == 10


def test_multi_year_history_caps_longevity_at_max():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=30,
            first_project_date=date(2000, 1, 1),
            last_project_date=date(2024, 1, 1),  # 24 years, far beyond 8y saturation
        ),
        reference_date=REF,
    )
    assert _factor(result, "longevity").points == LONGEVITY_MAX_POINTS


def test_longevity_spans_permit_and_award_dates_together():
    """Longevity's span uses the union of permit and award dates -- this
    is legitimate (it's about observed time span, not a count), unlike
    permit_depth/award_depth which must never be combined."""
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=3,
            first_project_date=date(2020, 1, 1),
            last_project_date=date(2020, 6, 1),
            award_count=2,
            first_award_date=date(2010, 1, 1),
            last_award_date=date(2011, 1, 1),
        ),
        reference_date=REF,
    )
    # earliest = 2010-01-01 (from awards), latest = 2020-06-01 (from permits)
    factor = _factor(result, "longevity")
    assert factor.points == LONGEVITY_MAX_POINTS  # >8 years spanned


# ===================================================================
# 4. Recency -- plateau / decay / floor / unknown
# ===================================================================


@pytest.mark.parametrize(
    "age_days,expected_points",
    [
        (0, RECENCY_MAX_POINTS),
        (30, RECENCY_MAX_POINTS),
        (RECENCY_PLATEAU_DAYS, RECENCY_MAX_POINTS),  # 730 -- still plateau
        (900, 13),  # mid-decay
        (1277, 9),  # deeper decay
        (RECENCY_DECAY_END_DAYS, RECENCY_FLOOR_POINTS),  # 1825 -- floor
        (2000, RECENCY_FLOOR_POINTS),  # beyond decay end -- still floor, never 0
    ],
)
def test_recency_plateau_decay_floor(age_days, expected_points):
    last_date = date(2020, 1, 1)
    reference = date.fromordinal(last_date.toordinal() + age_days)
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=last_date),
        reference_date=reference,
    )
    assert _factor(result, "recency").points == expected_points


def test_recency_unknown_when_no_date_available():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1),  # count>0 but no dates at all
        reference_date=REF,
    )
    factor = _factor(result, "recency")
    assert factor.points == 0
    assert "No activity date available" in factor.detail
    assert result.coverage.has_recency_signal is False


def test_recency_uses_most_recent_of_project_award_and_activity_dates():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=date(2010, 1, 1),
            award_count=1,
            last_award_date=date(2024, 6, 1),  # most recent
            last_activity_at=date(2015, 1, 1),
        ),
        reference_date=date(2024, 7, 1),  # 30 days after the award date
    )
    assert _factor(result, "recency").points == RECENCY_MAX_POINTS


def test_recency_never_below_floor_even_for_very_old_activity():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=date(1990, 1, 1)),
        reference_date=REF,
    )
    assert _factor(result, "recency").points == RECENCY_FLOOR_POINTS
    assert _factor(result, "recency").points > 0


# ===================================================================
# 5. Google reputation bonus -- absent / rating / review-volume boundary
# ===================================================================


def test_google_absent_gives_zero_not_penalty():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    factor = _factor(result, "google_reputation")
    assert factor.points == 0
    assert "not penalized" in factor.detail
    assert result.coverage.has_google_signal is False


@pytest.mark.parametrize(
    "rating,reviews,expected_points",
    [
        (5.0, 25, 14),  # full rating + full volume confidence -> cap
        (5.0, 0, 7),  # full rating, zero reviews -> floor confidence (0.5)
        (3.0, 25, 8),  # moderate rating, full volume confidence
        (4.5, 0, 6),  # review-volume floor boundary
        (4.5, 25, 13),  # review-volume saturation boundary -- same rating, more reviews
    ],
)
def test_google_reputation_rating_and_review_volume_boundaries(
    rating, reviews, expected_points
):
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=REF,
            google_rating=rating,
            google_reviews_count=reviews,
        ),
        reference_date=REF,
    )
    assert _factor(result, "google_reputation").points == expected_points


def test_google_review_volume_increases_points_at_same_rating():
    low = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=REF,
            google_rating=4.5,
            google_reviews_count=0,
        ),
        reference_date=REF,
    )
    high = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=REF,
            google_rating=4.5,
            google_reviews_count=25,
        ),
        reference_date=REF,
    )
    assert (
        _factor(high, "google_reputation").points
        > _factor(low, "google_reputation").points
    )


def test_google_reputation_never_exceeds_cap():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=REF,
            google_rating=5.0,
            google_reviews_count=100_000,
        ),
        reference_date=REF,
    )
    assert _factor(result, "google_reputation").points == GOOGLE_BONUS_MAX_POINTS


# ===================================================================
# 6. Buyer diversity bonus -- absent / present / repeat-client
# ===================================================================


def test_buyer_diversity_absent_gives_zero_not_penalty():
    result = score_company_track_record(
        CompanyTrackRecordInput(award_count=1, last_award_date=REF),
        reference_date=REF,
    )
    factor = _factor(result, "buyer_diversity")
    assert factor.points == 0
    assert "not penalized" in factor.detail
    assert result.coverage.has_buyer_diversity_signal is False


def test_buyer_diversity_present_no_repeat():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            award_count=1, last_award_date=REF, distinct_buyer_count=1
        ),
        reference_date=REF,
    )
    factor = _factor(result, "buyer_diversity")
    assert factor.points == 1
    assert "repeat business" not in factor.detail


def test_buyer_diversity_repeat_client_scenario():
    """award_count > distinct_buyer_count implies at least one repeat buyer."""
    result = score_company_track_record(
        CompanyTrackRecordInput(
            award_count=5, last_award_date=REF, distinct_buyer_count=2
        ),
        reference_date=REF,
    )
    factor = _factor(result, "buyer_diversity")
    assert factor.points == 4
    assert "repeat business detected" in factor.detail


def test_buyer_diversity_caps_at_max():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            award_count=1000, last_award_date=REF, distinct_buyer_count=500
        ),
        reference_date=REF,
    )
    assert _factor(result, "buyer_diversity").points == BUYER_DIVERSITY_BONUS_MAX_POINTS


def test_buyer_diversity_requires_both_award_count_and_distinct_count():
    """distinct_buyer_count alone (award_count=0) must not award credit.

    Since input validation now enforces distinct_buyer_count <=
    award_count, distinct_buyer_count > 0 with award_count == 0 can never
    reach the scoring layer at all -- it is rejected fail-closed. The
    only remaining reachable "distinct without award credit" case is
    award_count == 0 and distinct_buyer_count == 0, covered by
    test_buyer_diversity_absent_gives_zero_not_penalty.
    """
    _expect_invalid(
        total_projects=1,
        last_project_date=REF,
        distinct_buyer_count=5,
        award_count=0,
    )


# ===================================================================
# 7. Caps -- overall score never exceeds 100
# ===================================================================


def test_score_never_exceeds_total_max_points():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1000,
            first_project_date=date(2000, 1, 1),
            last_project_date=REF,
            award_count=1000,
            first_award_date=date(2000, 1, 1),
            last_award_date=REF,
            distinct_buyer_count=500,
            google_rating=5.0,
            google_reviews_count=100_000,
        ),
        reference_date=REF,
    )
    assert result.score == TOTAL_MAX_POINTS == 100


def test_max_points_across_factors_sum_to_one_hundred():
    assert (
        CORE_MAX_POINTS + GOOGLE_BONUS_MAX_POINTS + BUYER_DIVERSITY_BONUS_MAX_POINTS
        == 100
    )


# ===================================================================
# 8. Breakdown-sum invariant, coverage, reasons
# ===================================================================


def test_score_always_equals_sum_of_breakdown_points():
    scenarios = [
        CompanyTrackRecordInput(total_projects=5, last_project_date=REF),
        CompanyTrackRecordInput(
            award_count=3, last_award_date=REF, distinct_buyer_count=2
        ),
        CompanyTrackRecordInput(
            total_projects=12,
            first_project_date=date(2015, 1, 1),
            last_project_date=date(2023, 1, 1),
            award_count=4,
            distinct_buyer_count=3,
            google_rating=4.2,
            google_reviews_count=15,
        ),
    ]
    for input_ in scenarios:
        result = score_company_track_record(input_, reference_date=REF)
        assert result.score == sum(f.points for f in result.breakdown)


def test_coverage_bonus_factors_present_count():
    neither = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    assert neither.coverage.bonus_factors_present == 0

    google_only = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=REF,
            google_rating=4.0,
            google_reviews_count=5,
        ),
        reference_date=REF,
    )
    assert google_only.coverage.bonus_factors_present == 1

    both = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1,
            last_project_date=REF,
            google_rating=4.0,
            google_reviews_count=5,
            award_count=2,
            distinct_buyer_count=1,
        ),
        reference_date=REF,
    )
    assert both.coverage.bonus_factors_present == 2


def test_reasons_are_top_active_factor_labels_sorted_by_points():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=30,
            first_project_date=date(2012, 1, 1),
            last_project_date=date(2024, 6, 1),
            award_count=8,
            first_award_date=date(2015, 1, 1),
            last_award_date=date(2023, 1, 1),
            distinct_buyer_count=4,
            google_rating=4.6,
            google_reviews_count=40,
        ),
        reference_date=date(2024, 7, 1),
    )
    assert result.reasons == (
        "Permit activity depth",
        "Observed activity span",
        "Recency of most recent activity",
        "Public award activity depth",
        "Google public reputation",
    )


def test_reasons_empty_when_no_core_evidence():
    result = score_company_track_record(CompanyTrackRecordInput(), reference_date=REF)
    assert result.reasons == ()


# ===================================================================
# 9. Golden full breakdowns
# ===================================================================


def test_golden_established_general_contractor_full_breakdown():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=30,
            first_project_date=date(2012, 1, 1),
            last_project_date=date(2024, 6, 1),
            award_count=8,
            first_award_date=date(2015, 1, 1),
            last_award_date=date(2023, 1, 1),
            distinct_buyer_count=4,
            google_rating=4.6,
            google_reviews_count=40,
        ),
        reference_date=date(2024, 7, 1),
    )
    assert result.score == 97
    points_by_factor = {f.factor: f.points for f in result.breakdown}
    assert points_by_factor == {
        "permit_depth": 30,
        "award_depth": 14,
        "longevity": 20,
        "recency": 15,
        "google_reputation": 13,
        "buyer_diversity": 5,
    }
    assert result.coverage.to_dict() == {
        "core_evidence_present": True,
        "has_permit_evidence": True,
        "has_award_evidence": True,
        "has_recency_signal": True,
        "has_google_signal": True,
        "has_buyer_diversity_signal": True,
        "bonus_factors_present": 2,
    }


def test_golden_sparse_permit_only_company_full_breakdown():
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=3,
            first_project_date=date(2023, 1, 1),
            last_project_date=date(2023, 6, 1),
        ),
        reference_date=date(2023, 6, 15),
    )
    assert result.score == 29
    points_by_factor = {f.factor: f.points for f in result.breakdown}
    assert points_by_factor == {
        "permit_depth": 13,
        "award_depth": 0,
        "longevity": 1,
        "recency": 15,
        "google_reputation": 0,
        "buyer_diversity": 0,
    }
    assert result.coverage.to_dict() == {
        "core_evidence_present": True,
        "has_permit_evidence": True,
        "has_award_evidence": False,
        "has_recency_signal": True,
        "has_google_signal": False,
        "has_buyer_diversity_signal": False,
        "bonus_factors_present": 0,
    }


# ===================================================================
# 10. Determinism -- byte-identical serialization, reference_date isolation
# ===================================================================


def test_identical_input_and_reference_date_gives_byte_identical_serialization():
    input_a = CompanyTrackRecordInput(
        total_projects=10,
        first_project_date=date(2018, 1, 1),
        last_project_date=date(2023, 1, 1),
        award_count=3,
        distinct_buyer_count=2,
        google_rating=4.3,
        google_reviews_count=12,
    )
    input_b = CompanyTrackRecordInput(
        total_projects=10,
        first_project_date=date(2018, 1, 1),
        last_project_date=date(2023, 1, 1),
        award_count=3,
        distinct_buyer_count=2,
        google_rating=4.3,
        google_reviews_count=12,
    )
    assert input_a is not input_b
    assert input_a == input_b  # dataclass value equality

    result_a = score_company_track_record(input_a, reference_date=REF)
    result_b = score_company_track_record(input_b, reference_date=REF)

    json_a = json.dumps(result_a.to_dict(), sort_keys=True)
    json_b = json.dumps(result_b.to_dict(), sort_keys=True)
    assert json_a == json_b
    assert result_a.to_dict() == result_b.to_dict()


def test_calling_twice_with_same_input_is_deterministic():
    input_ = CompanyTrackRecordInput(total_projects=7, last_project_date=REF)
    first = score_company_track_record(input_, reference_date=REF)
    second = score_company_track_record(input_, reference_date=REF)
    assert first.to_dict() == second.to_dict()


def test_changing_only_reference_date_affects_only_recency():
    input_ = CompanyTrackRecordInput(
        total_projects=15,
        first_project_date=date(2015, 1, 1),
        last_project_date=date(2020, 1, 1),
        award_count=4,
        distinct_buyer_count=3,
        google_rating=4.4,
        google_reviews_count=20,
    )
    near = score_company_track_record(
        input_, reference_date=date(2020, 2, 1)
    )  # 31 days later
    far = score_company_track_record(
        input_, reference_date=date(2024, 2, 1)
    )  # ~4 years later

    near_by_factor = {f.factor: (f.points, f.detail) for f in near.breakdown}
    far_by_factor = {f.factor: (f.points, f.detail) for f in far.breakdown}

    assert near_by_factor["recency"] != far_by_factor["recency"]
    for key in (
        "permit_depth",
        "award_depth",
        "longevity",
        "google_reputation",
        "buyer_diversity",
    ):
        assert near_by_factor[key] == far_by_factor[key], key

    # Coverage does not depend on reference_date either.
    assert near.coverage == far.coverage


# ===================================================================
# 11. Algorithm version -- presence and stability
# ===================================================================


def test_algorithm_version_present_and_matches_constant():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    assert result.algorithm_version == COMPANY_TRACK_RECORD_ALGORITHM_VERSION
    assert result.algorithm_version == "company_track_record_v1"


def test_algorithm_version_stable_across_all_scenarios():
    scenarios = [
        CompanyTrackRecordInput(),
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        CompanyTrackRecordInput(award_count=1, last_award_date=REF),
        CompanyTrackRecordInput(
            total_projects=50,
            first_project_date=date(2000, 1, 1),
            last_project_date=REF,
            award_count=20,
            distinct_buyer_count=10,
            google_rating=4.9,
            google_reviews_count=200,
        ),
    ]
    versions = {
        score_company_track_record(s, reference_date=REF).algorithm_version
        for s in scenarios
    }
    assert versions == {"company_track_record_v1"}


def test_algorithm_version_present_in_serialized_dict():
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    assert result.to_dict()["algorithm_version"] == "company_track_record_v1"


# ===================================================================
# 12. Fail-closed input validation -- invalid input raises, never clamps
# ===================================================================


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("total_projects", -1),
        ("total_projects", "5"),
        ("total_projects", 5.0),
        ("total_projects", True),
        ("total_projects", False),
        ("award_count", -1),
        ("award_count", "3"),
        ("award_count", 2.5),
        ("award_count", True),
        ("distinct_buyer_count", -1),
        ("distinct_buyer_count", "2"),
        ("distinct_buyer_count", 1.5),
        ("distinct_buyer_count", True),
    ],
)
def test_invalid_count_fields_raise(field_name, bad_value):
    kwargs = {"total_projects": 1, "last_project_date": REF, field_name: bad_value}
    _expect_invalid(**kwargs)


def test_distinct_buyer_count_exceeding_award_count_raises():
    _expect_invalid(award_count=2, distinct_buyer_count=3)


def test_distinct_buyer_count_equal_to_award_count_is_valid():
    """Boundary: equality is allowed, only '>' is rejected."""
    result = score_company_track_record(
        CompanyTrackRecordInput(
            award_count=2, last_award_date=REF, distinct_buyer_count=2
        ),
        reference_date=REF,
    )
    assert result.score is not None


@pytest.mark.parametrize(
    "bad_rating",
    [-0.1, 5.1, float("nan"), float("inf"), float("-inf"), "4.5", True, False],
)
def test_invalid_google_rating_raises(bad_rating):
    _expect_invalid(total_projects=1, last_project_date=REF, google_rating=bad_rating)


def test_google_rating_boundary_values_are_valid():
    for boundary in (0.0, 5.0):
        result = score_company_track_record(
            CompanyTrackRecordInput(
                total_projects=1, last_project_date=REF, google_rating=boundary
            ),
            reference_date=REF,
        )
        assert result.score is not None


@pytest.mark.parametrize("bad_reviews", [-1, "10", 2.5, True, False])
def test_invalid_google_reviews_count_raises(bad_reviews):
    _expect_invalid(
        total_projects=1,
        last_project_date=REF,
        google_rating=4.0,
        google_reviews_count=bad_reviews,
    )


def test_first_project_date_after_last_project_date_raises():
    _expect_invalid(
        total_projects=2,
        first_project_date=date(2022, 1, 1),
        last_project_date=date(2021, 1, 1),
    )


def test_first_award_date_after_last_award_date_raises():
    _expect_invalid(
        award_count=2,
        first_award_date=date(2022, 1, 1),
        last_award_date=date(2021, 1, 1),
    )


def test_equal_first_and_last_dates_are_valid():
    """Boundary: first == last is allowed (a single-day record span)."""
    result = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=2,
            first_project_date=date(2021, 1, 1),
            last_project_date=date(2021, 1, 1),
        ),
        reference_date=REF,
    )
    assert result.score is not None


@pytest.mark.parametrize(
    "field_name",
    [
        "first_project_date",
        "last_project_date",
        "first_award_date",
        "last_award_date",
        "last_activity_at",
    ],
)
def test_activity_date_after_reference_date_raises(field_name):
    future = date(2030, 1, 1)
    kwargs = {"total_projects": 1, "award_count": 1, field_name: future}
    _expect_invalid(**kwargs)


def test_activity_date_equal_to_reference_date_is_valid():
    """Boundary: exactly reference_date is allowed, only strictly-after is rejected."""
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=REF,
    )
    assert result.score is not None


def test_invalid_input_error_is_a_value_error():
    assert issubclass(InvalidCompanyTrackRecordInputError, ValueError)


def test_invalid_input_raises_before_any_scoring_side_effect():
    """Validation happens up front -- an invalid field on an otherwise
    perfectly scoreable company still raises, it is not clamped away."""
    with pytest.raises(InvalidCompanyTrackRecordInputError):
        score_company_track_record(
            CompanyTrackRecordInput(
                total_projects=50,
                first_project_date=date(2000, 1, 1),
                last_project_date=date(2024, 1, 1),
                award_count=20,
                distinct_buyer_count=10,
                google_rating=4.9,
                google_reviews_count=-5,  # the one invalid field
            ),
            reference_date=REF,
        )


# ===================================================================
# 13. Coverage consistency -- honest reporting even when score is None
# ===================================================================


def test_coverage_consistent_between_none_and_scored_paths_for_bonus_signals():
    """The same bonus-signal input must report the same coverage booleans
    whether or not core evidence happens to be present."""
    shared_google = dict(google_rating=4.4, google_reviews_count=30)

    without_core = score_company_track_record(
        CompanyTrackRecordInput(**shared_google), reference_date=REF
    )
    with_core = score_company_track_record(
        CompanyTrackRecordInput(
            total_projects=1, last_project_date=REF, **shared_google
        ),
        reference_date=REF,
    )

    assert without_core.score is None
    assert with_core.score is not None
    assert (
        without_core.coverage.has_google_signal
        == with_core.coverage.has_google_signal
        is True
    )
    assert (
        without_core.coverage.bonus_factors_present
        == with_core.coverage.bonus_factors_present
        == 1
    )


def test_coverage_recency_signal_honest_when_score_is_none():
    """last_activity_at can exist even without core evidence -- coverage
    must reflect that honestly rather than hardcoding False."""
    result = score_company_track_record(
        CompanyTrackRecordInput(last_activity_at=REF),
        reference_date=REF,
    )
    assert result.score is None
    assert result.coverage.has_recency_signal is True


def test_coverage_all_false_when_truly_empty_input():
    result = score_company_track_record(CompanyTrackRecordInput(), reference_date=REF)
    assert result.coverage.core_evidence_present is False
    assert result.coverage.has_permit_evidence is False
    assert result.coverage.has_award_evidence is False
    assert result.coverage.has_recency_signal is False
    assert result.coverage.has_google_signal is False
    assert result.coverage.has_buyer_diversity_signal is False
    assert result.coverage.bonus_factors_present == 0


# ===================================================================
# 14. Type contract hardening -- input_ / reference_date / date fields
#     must be exactly CompanyTrackRecordInput / datetime.date, never a
#     bare AttributeError/TypeError escapes from wrong-typed input.
# ===================================================================

_DATE_FIELD_NAMES = (
    "first_project_date",
    "last_project_date",
    "first_award_date",
    "last_award_date",
    "last_activity_at",
)


@pytest.mark.parametrize(
    "bad_input",
    [None, {}, "input", 42, ["not", "input"], object()],
)
def test_wrong_input_type_raises_invalid_error_not_attribute_error(bad_input):
    with pytest.raises(InvalidCompanyTrackRecordInputError):
        score_company_track_record(bad_input, reference_date=REF)


def test_string_reference_date_raises():
    _expect_invalid(
        reference_date="2024-07-01",
        total_projects=1,
        last_project_date=REF,
    )


def test_datetime_reference_date_raises():
    _expect_invalid(
        reference_date=datetime(2024, 7, 1, 12, 0, 0),
        total_projects=1,
        last_project_date=REF,
    )


@pytest.mark.parametrize("field_name", _DATE_FIELD_NAMES)
def test_string_date_field_raises(field_name):
    kwargs = {"total_projects": 1, "award_count": 1, field_name: "2020-01-01"}
    _expect_invalid(**kwargs)


@pytest.mark.parametrize("field_name", _DATE_FIELD_NAMES)
def test_datetime_date_field_raises(field_name):
    kwargs = {
        "total_projects": 1,
        "award_count": 1,
        field_name: datetime(2020, 1, 1, 8, 30, 0),
    }
    _expect_invalid(**kwargs)


def test_valid_date_reference_date_does_not_raise():
    """Boundary: a plain datetime.date reference_date is accepted."""
    result = score_company_track_record(
        CompanyTrackRecordInput(total_projects=1, last_project_date=REF),
        reference_date=date(2024, 7, 1),
    )
    assert result.score is not None
