"""Tests for the PR-G3.1 track-record adapter + assignment helper
(pipeline/company_track_record.py).

No DB, no session, no network, no LLM/API calls anywhere in this file --
the module under test performs none of these, and this suite proves it
both behaviorally (fake/duck-typed companies) and structurally (source
inspection for forbidden calls).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from db.models import Company
from pipeline.company_track_record import (
    CompanyTrackRecordAdapterError,
    TrackRecordAdapterDiagnostics,
    TrackRecordAdapterResult,
    WRITABLE_TRACK_RECORD_COLUMNS,
    assign_track_record_result,
    build_company_track_record_input,
)
from pipeline.scoring.company_track_record import (
    CompanyTrackRecordResult,
    score_company_track_record,
)

MODULE_FILE = (
    Path(__file__).resolve().parents[2] / "pipeline" / "company_track_record.py"
)

REFERENCE_DATE = date(2026, 1, 1)


@dataclass
class _FakeCompany:
    """Minimal duck-typed stand-in for db.models.Company -- exposes only
    the attributes the adapter reads."""

    total_projects: Any = 0
    first_project_date: Any = ""
    last_project_date: Any = ""
    award_count: Any = 0
    first_award_date: Any = ""
    last_award_date: Any = ""
    award_clients: Any = None
    last_activity_at: Any = None
    google_rating: Any = None
    google_reviews_count: Any = None


class _PoisonedCompany:
    """Exposes valid core fields, but raises if any AI/derived-scoring
    column is ever accessed -- proves the adapter never reads them."""

    total_projects = 5
    first_project_date = "2020-01-01"
    last_project_date = "2021-01-01"
    award_count = 2
    first_award_date = "2020-06-01"
    last_award_date = "2020-12-01"
    award_clients = ["City of Vancouver", "City of Burnaby"]
    last_activity_at = datetime(2021, 1, 1, tzinfo=timezone.utc)
    google_rating = 4.5
    google_reviews_count = 10

    @property
    def ai_reliability_score(self):
        raise AssertionError("adapter must never read ai_reliability_score")

    @property
    def ai_summary(self):
        raise AssertionError("adapter must never read ai_summary")

    @property
    def construction_score(self):
        raise AssertionError("adapter must never read construction_score")

    @property
    def construction_tier_json(self):
        raise AssertionError("adapter must never read construction_tier_json")

    @property
    def cip_json(self):
        raise AssertionError("adapter must never read cip_json")

    @property
    def capability_profile_json(self):
        raise AssertionError("adapter must never read capability_profile_json")


# ===================================================================
# 1. Full happy-path mapping
# ===================================================================


def test_full_field_mapping_happy_path():
    company = _FakeCompany(
        total_projects=12,
        first_project_date="2018-03-01",
        last_project_date="2024-05-01",
        award_count=4,
        first_award_date="2019-01-15",
        last_award_date="2023-11-01",
        award_clients=["City of Vancouver", "City of Burnaby", "Province of BC"],
        last_activity_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        google_rating=4.3,
        google_reviews_count=27,
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert isinstance(result, TrackRecordAdapterResult)
    input_ = result.input
    assert input_.total_projects == 12
    assert input_.first_project_date == date(2018, 3, 1)
    assert input_.last_project_date == date(2024, 5, 1)
    assert input_.award_count == 4
    assert input_.first_award_date == date(2019, 1, 15)
    assert input_.last_award_date == date(2023, 11, 1)
    assert input_.distinct_buyer_count == 3
    assert input_.last_activity_at == date(2024, 6, 1)
    assert input_.google_rating == 4.3
    assert input_.google_reviews_count == 27
    assert result.diagnostics.notes == ()

    # And the resulting input is genuinely scorer-ready.
    scored = score_company_track_record(input_, reference_date=REFERENCE_DATE)
    assert scored.score is not None


def test_adapter_input_is_accepted_without_error_by_the_real_orm_company():
    """Proves adapter compatibility against the real db.models.Company
    class (in-memory construction only -- no DB session, no flush)."""
    company = Company(
        name="Real ORM Co",
        total_projects=5,
        first_project_date="2015-01-01",
        last_project_date="2020-01-01",
        award_count=3,
        first_award_date="2016-01-01",
        last_award_date="2019-01-01",
        award_clients=["City of Vancouver", "City of Burnaby", "City of Vancouver"],
        last_activity_at=datetime(2020, 6, 1, tzinfo=timezone.utc),
        google_rating=4.2,
        google_reviews_count=30,
    )
    result = build_company_track_record_input(company, reference_date=date(2021, 1, 1))
    assert result.input.total_projects == 5
    assert result.input.distinct_buyer_count == 2  # "City of Vancouver" deduped
    scored = score_company_track_record(result.input, reference_date=date(2021, 1, 1))
    assert scored.score is not None


# ===================================================================
# 2. Structural count validation -- typed adapter errors
# ===================================================================


def test_none_counts_default_to_zero_not_an_error():
    company = _FakeCompany(total_projects=None, award_count=None)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.total_projects == 0
    assert result.input.award_count == 0


@pytest.mark.parametrize("bad_value", [-1, "5", 5.0, [5], {}, True, False])
def test_structurally_invalid_total_projects_raises_typed_error(bad_value):
    company = _FakeCompany(total_projects=bad_value)
    with pytest.raises(CompanyTrackRecordAdapterError):
        build_company_track_record_input(company, reference_date=REFERENCE_DATE)


@pytest.mark.parametrize("bad_value", [-1, "3", 3.0, [3], {}, True, False])
def test_structurally_invalid_award_count_raises_typed_error(bad_value):
    company = _FakeCompany(award_count=bad_value)
    with pytest.raises(CompanyTrackRecordAdapterError):
        build_company_track_record_input(company, reference_date=REFERENCE_DATE)


# ===================================================================
# 3. Date parsing -- empty/malformed and diagnostics
# ===================================================================


@pytest.mark.parametrize(
    "field",
    ["first_project_date", "last_project_date", "first_award_date", "last_award_date"],
)
def test_empty_string_date_maps_to_none_with_no_diagnostic(field):
    company = _FakeCompany(**{field: ""})
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert getattr(result.input, field) is None
    assert result.diagnostics.notes == ()


@pytest.mark.parametrize(
    "field",
    ["first_project_date", "last_project_date", "first_award_date", "last_award_date"],
)
def test_malformed_date_string_dropped_with_diagnostic(field):
    company = _FakeCompany(**{field: "not-a-date"})
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert getattr(result.input, field) is None
    assert any(field in note and "dropped" in note for note in result.diagnostics.notes)


def test_non_string_date_field_dropped_with_diagnostic():
    company = _FakeCompany(first_project_date=20200101)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.first_project_date is None
    assert any("first_project_date" in note for note in result.diagnostics.notes)


def test_date_string_with_time_component_parses_via_first_ten_chars():
    company = _FakeCompany(first_project_date="2020-01-01T00:00:00Z")
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.first_project_date == date(2020, 1, 1)


# ===================================================================
# 4. last_activity_at -- timezone conversion + naive handling
# ===================================================================


def test_last_activity_at_utc_passthrough():
    company = _FakeCompany(
        last_activity_at=datetime(2025, 6, 15, 3, 0, tzinfo=timezone.utc)
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.last_activity_at == date(2025, 6, 15)


def test_last_activity_at_non_utc_converted_to_utc_date():
    # 23:30 in UTC-5 on June 15 is 04:30 UTC on June 16 -- proves real
    # timezone conversion, not just a naive .date() call.
    tz = timezone(timedelta(hours=-5))
    company = _FakeCompany(last_activity_at=datetime(2025, 6, 15, 23, 30, tzinfo=tz))
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.last_activity_at == date(2025, 6, 16)


def test_last_activity_at_naive_dropped_with_diagnostic_not_raised():
    company = _FakeCompany(last_activity_at=datetime(2025, 6, 15, 12, 0))
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.last_activity_at is None
    assert any("naive" in note for note in result.diagnostics.notes)


def test_last_activity_at_wrong_type_dropped_with_diagnostic():
    company = _FakeCompany(last_activity_at="2025-06-15")
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.last_activity_at is None
    assert any("last_activity_at" in note for note in result.diagnostics.notes)


# ===================================================================
# 5. Invalid / future / misordered dates
# ===================================================================


def test_future_date_dropped_with_diagnostic():
    future = (REFERENCE_DATE + timedelta(days=30)).isoformat()
    company = _FakeCompany(first_project_date=future)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.first_project_date is None
    assert any("after reference_date" in note for note in result.diagnostics.notes)


def test_future_last_activity_at_dropped():
    future = datetime.combine(
        REFERENCE_DATE + timedelta(days=5), datetime.min.time(), tzinfo=timezone.utc
    )
    company = _FakeCompany(last_activity_at=future)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.last_activity_at is None


def test_misordered_project_dates_both_dropped():
    company = _FakeCompany(
        first_project_date="2024-01-01", last_project_date="2020-01-01"
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.first_project_date is None
    assert result.input.last_project_date is None
    assert any("after" in note for note in result.diagnostics.notes)


def test_misordered_award_dates_both_dropped():
    company = _FakeCompany(first_award_date="2024-01-01", last_award_date="2020-01-01")
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.first_award_date is None
    assert result.input.last_award_date is None


def test_reference_date_must_be_a_date_not_datetime():
    company = _FakeCompany()
    with pytest.raises(CompanyTrackRecordAdapterError):
        build_company_track_record_input(
            company, reference_date=datetime.now(timezone.utc)
        )


# ===================================================================
# 6. award_clients normalization
# ===================================================================


def test_award_clients_none_yields_zero_distinct_buyers():
    company = _FakeCompany(award_clients=None, award_count=3)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 0


def test_award_clients_empty_list_yields_zero_distinct_buyers():
    company = _FakeCompany(award_clients=[], award_count=3)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 0


def test_award_clients_duplicates_deduplicated():
    company = _FakeCompany(
        award_clients=["City of Vancouver", "City of Vancouver", "City of Burnaby"],
        award_count=5,
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 2


def test_award_clients_empty_strings_dropped_with_diagnostic():
    company = _FakeCompany(
        award_clients=["City of Vancouver", "", "   "], award_count=5
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 1
    assert any("empty" in note for note in result.diagnostics.notes)


def test_award_clients_legacy_malformed_elements_dropped_with_diagnostic():
    company = _FakeCompany(
        award_clients=["City of Vancouver", None, 42, {"name": "bad"}],
        award_count=5,
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 1
    assert any("non-string" in note for note in result.diagnostics.notes)


def test_award_clients_whitespace_stripped_before_dedup():
    company = _FakeCompany(
        award_clients=["City of Vancouver", "  City of Vancouver  "],
        award_count=5,
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 1


def test_award_clients_tuple_container_accepted_like_a_list():
    company = _FakeCompany(
        award_clients=("City of Vancouver", "City of Burnaby"),
        award_count=5,
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 2
    assert result.diagnostics.notes == ()


def test_award_clients_empty_tuple_yields_zero_distinct_buyers():
    company = _FakeCompany(award_clients=(), award_count=3)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 0
    assert result.diagnostics.notes == ()


@pytest.mark.parametrize(
    "bad_container",
    [
        "City of Vancouver",  # str -- iterable, must not be walked char-by-char
        b"City of Vancouver",  # bytes -- iterable, must not be walked byte-by-byte
        5,  # int -- not iterable at all
        {"City of Vancouver": 1},  # dict -- iterable (over keys), wrong shape
        object(),  # arbitrary non-iterable object
    ],
    ids=["str", "bytes", "int", "dict", "object"],
)
def test_award_clients_wrong_container_type_never_iterated_never_raises(bad_container):
    company = _FakeCompany(award_clients=bad_container, award_count=5)
    # Must not raise TypeError (or anything else) for any of these shapes.
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 0
    assert any(
        "award_clients" in note and type(bad_container).__name__ in note
        for note in result.diagnostics.notes
    )


# ===================================================================
# 7. distinct_buyer_count never exceeds award_count
# ===================================================================


def test_distinct_buyer_count_capped_at_award_count():
    company = _FakeCompany(
        award_clients=["A", "B", "C", "D", "E"],
        award_count=2,
    )
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 2
    assert any("capped at award_count" in note for note in result.diagnostics.notes)
    # And the scorer's own invariant is never violated.
    score_company_track_record(result.input, reference_date=REFERENCE_DATE)


def test_distinct_buyer_count_capped_even_when_award_count_zero():
    company = _FakeCompany(award_clients=["A", "B"], award_count=0)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.distinct_buyer_count == 0


# ===================================================================
# 8. Google boundary / invalid values
# ===================================================================


@pytest.mark.parametrize("rating", [0.0, 5.0, 2.5])
def test_google_rating_within_bounds_passthrough(rating):
    company = _FakeCompany(google_rating=rating)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_rating == rating
    assert result.diagnostics.notes == ()


@pytest.mark.parametrize("rating", [-0.1, 5.1, 100.0])
def test_google_rating_out_of_bounds_dropped_with_diagnostic(rating):
    company = _FakeCompany(google_rating=rating)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_rating is None
    assert any("google_rating" in note for note in result.diagnostics.notes)


def test_google_rating_nan_dropped():
    company = _FakeCompany(google_rating=float("nan"))
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_rating is None


def test_google_rating_bool_dropped_with_diagnostic():
    company = _FakeCompany(google_rating=True)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_rating is None
    assert any("google_rating" in note for note in result.diagnostics.notes)


def test_google_rating_non_numeric_dropped_with_diagnostic():
    company = _FakeCompany(google_rating="4.5")
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_rating is None


def test_google_reviews_count_negative_dropped_with_diagnostic():
    company = _FakeCompany(google_reviews_count=-5)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_reviews_count is None
    assert any("google_reviews_count" in note for note in result.diagnostics.notes)


def test_google_reviews_count_bool_dropped():
    company = _FakeCompany(google_reviews_count=False)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_reviews_count is None


def test_google_reviews_count_non_int_dropped():
    company = _FakeCompany(google_reviews_count=12.5)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.google_reviews_count is None


# ===================================================================
# 9. Adapter never reads AI/derived-scoring fields
# ===================================================================


def test_adapter_never_touches_ai_or_cip_or_construction_fields():
    company = _PoisonedCompany()
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    assert result.input.total_projects == 5  # would have raised already if poisoned


# ===================================================================
# 10. score=None coherent computed state
# ===================================================================


def test_no_core_evidence_still_produces_scorer_ready_input():
    company = _FakeCompany(total_projects=0, award_count=0)
    result = build_company_track_record_input(company, reference_date=REFERENCE_DATE)
    scored = score_company_track_record(result.input, reference_date=REFERENCE_DATE)
    assert scored.score is None
    # And the assignment helper still fills all three "computed" fields.
    target = _FakeCompany()
    computed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assign_track_record_result(target, scored, computed_at=computed_at)
    assert target.track_record_score is None
    assert target.track_record_json is not None
    assert target.track_record_at == computed_at
    assert target.track_record_version == "company_track_record_v1"


# ===================================================================
# 11. Determinism
# ===================================================================


def test_identical_inputs_produce_byte_identical_adapter_result():
    company_a = _FakeCompany(
        total_projects=8,
        first_project_date="2019-01-01",
        last_project_date="2023-01-01",
        award_count=2,
        award_clients=["City of Vancouver"],
        last_activity_at=datetime(2023, 6, 1, tzinfo=timezone.utc),
        google_rating=4.1,
        google_reviews_count=9,
    )
    company_b = _FakeCompany(**company_a.__dict__)

    result_a = build_company_track_record_input(
        company_a, reference_date=REFERENCE_DATE
    )
    result_b = build_company_track_record_input(
        company_b, reference_date=REFERENCE_DATE
    )

    assert result_a.input == result_b.input
    assert result_a.diagnostics == result_b.diagnostics


def test_identical_score_and_computed_at_produce_identical_assignment():
    company = _FakeCompany(
        total_projects=8, award_count=2, award_clients=["City of Vancouver"]
    )
    input_ = build_company_track_record_input(
        company, reference_date=REFERENCE_DATE
    ).input
    scored = score_company_track_record(input_, reference_date=REFERENCE_DATE)

    computed_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    target_a = _FakeCompany()
    target_b = _FakeCompany()
    assign_track_record_result(target_a, scored, computed_at=computed_at)
    assign_track_record_result(target_b, scored, computed_at=computed_at)

    assert target_a.track_record_score == target_b.track_record_score
    assert target_a.track_record_json == target_b.track_record_json
    assert target_a.track_record_at == target_b.track_record_at
    assert target_a.track_record_version == target_b.track_record_version


# ===================================================================
# 12. assign_track_record_result -- allowlist, JSON contract, computed_at
# ===================================================================


def _scored_result_with_core_evidence() -> CompanyTrackRecordResult:
    company = _FakeCompany(
        total_projects=10,
        first_project_date="2018-01-01",
        last_project_date="2024-01-01",
        award_count=3,
        first_award_date="2019-01-01",
        last_award_date="2023-01-01",
        award_clients=["City of Vancouver", "City of Burnaby"],
        last_activity_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        google_rating=4.5,
        google_reviews_count=40,
    )
    input_ = build_company_track_record_input(
        company, reference_date=REFERENCE_DATE
    ).input
    return score_company_track_record(input_, reference_date=REFERENCE_DATE)


def test_writable_columns_allowlist_is_exactly_four_fields():
    assert WRITABLE_TRACK_RECORD_COLUMNS == {
        "track_record_score",
        "track_record_json",
        "track_record_at",
        "track_record_version",
    }


def test_assignment_sets_exactly_four_fields_and_no_others():
    company = Company(name="Assignment Test Co")
    before = {
        key: value for key, value in vars(company).items() if not key.startswith("_")
    }
    scored = _scored_result_with_core_evidence()
    computed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assign_track_record_result(company, scored, computed_at=computed_at)

    after = {
        key: value for key, value in vars(company).items() if not key.startswith("_")
    }
    changed_keys = {k for k in after if before.get(k) != after.get(k)} | (
        set(after) - set(before)
    )
    assert changed_keys <= WRITABLE_TRACK_RECORD_COLUMNS


def test_ai_and_cip_and_construction_fields_unchanged_by_assignment():
    company = Company(
        name="Untouched Fields Co",
        ai_reliability_score=88,
        ai_summary="A well-established firm.",
        construction_score=70,
        cip_json={"some": "profile"},
        capability_profile_json={"other": "profile"},
    )
    scored = _scored_result_with_core_evidence()
    assign_track_record_result(
        company, scored, computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )

    assert company.ai_reliability_score == 88
    assert company.ai_summary == "A well-established firm."
    assert company.construction_score == 70
    assert company.cip_json == {"some": "profile"}
    assert company.capability_profile_json == {"other": "profile"}


def test_full_json_contract_preserved_without_loss():
    scored = _scored_result_with_core_evidence()
    company = _FakeCompany()
    assign_track_record_result(
        company, scored, computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )

    payload = company.track_record_json
    assert payload == scored.to_dict()
    assert set(payload.keys()) == {
        "score",
        "breakdown",
        "reasons",
        "coverage",
        "reference_date",
        "algorithm_version",
    }
    assert payload["algorithm_version"] == "company_track_record_v1"
    assert isinstance(payload["breakdown"], list) and len(payload["breakdown"]) > 0
    assert isinstance(payload["coverage"], dict)


def test_computed_at_naive_raises_typed_error():
    scored = _scored_result_with_core_evidence()
    company = _FakeCompany()
    with pytest.raises(CompanyTrackRecordAdapterError):
        assign_track_record_result(company, scored, computed_at=datetime(2026, 1, 1))


def test_computed_at_non_utc_normalized_to_utc():
    scored = _scored_result_with_core_evidence()
    company = _FakeCompany()
    tz = timezone(timedelta(hours=-8))
    local_time = datetime(2026, 1, 1, 4, 0, tzinfo=tz)  # 12:00 UTC
    assign_track_record_result(company, scored, computed_at=local_time)
    assert company.track_record_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert company.track_record_at.tzinfo == timezone.utc


def test_computed_at_wrong_type_raises_typed_error():
    scored = _scored_result_with_core_evidence()
    company = _FakeCompany()
    with pytest.raises(CompanyTrackRecordAdapterError):
        assign_track_record_result(company, scored, computed_at="2026-01-01")


def test_result_wrong_type_raises_typed_error():
    company = _FakeCompany()
    with pytest.raises(CompanyTrackRecordAdapterError):
        assign_track_record_result(
            company,
            {"score": 50},
            computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_score_present_case_all_four_fields_set_consistently():
    scored = _scored_result_with_core_evidence()
    assert scored.score is not None
    company = _FakeCompany()
    computed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assign_track_record_result(company, scored, computed_at=computed_at)
    assert company.track_record_score == scored.score
    assert company.track_record_json is not None
    assert company.track_record_at == computed_at
    assert company.track_record_version == "company_track_record_v1"


# ===================================================================
# 13. Structural guard: no session/commit/network/API access anywhere
# ===================================================================


def test_module_source_never_references_session_commit_or_network():
    source = MODULE_FILE.read_text(encoding="utf-8")
    forbidden_substrings = (
        ".commit(",
        ".rollback(",
        ".flush(",
        "Session(",
        "session.execute",
        "select(",
        "import requests",
        "import anthropic",
        "get_session",
        "datetime.now(",
        "date.today(",
    )
    for forbidden in forbidden_substrings:
        assert (
            forbidden not in source
        ), f"forbidden pattern found in module: {forbidden!r}"
