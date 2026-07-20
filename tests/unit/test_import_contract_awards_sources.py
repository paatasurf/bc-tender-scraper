"""Source-window tests for official contract-award imports."""

from __future__ import annotations

from datetime import date

from pipeline.import_contract_awards import (
    FEDERAL_CSV_URL,
    FEDERAL_HISTORY_YEARS,
    _default_federal_years,
    _federal_years,
)


def test_default_federal_years_includes_current_fiscal_year_after_april() -> None:
    assert _default_federal_years(as_of=date(2026, 7, 20)) == (
        "2026-2027",
        "2025-2026",
        "2024-2025",
        "2023-2024",
        "2022-2023",
    )


def test_default_federal_years_uses_prior_start_year_before_april() -> None:
    assert _default_federal_years(as_of=date(2027, 3, 31))[0] == "2026-2027"
    assert _default_federal_years(as_of=date(2027, 4, 1))[0] == "2027-2028"


def test_default_federal_years_covers_current_and_four_prior() -> None:
    result = _default_federal_years(as_of=date(2026, 7, 20))
    assert len(result) == FEDERAL_HISTORY_YEARS == 5


def test_default_federal_years_has_no_duplicates() -> None:
    result = _default_federal_years(as_of=date(2026, 7, 20))
    assert len(set(result)) == len(result)


def test_default_federal_years_ordered_newest_first() -> None:
    result = _default_federal_years(as_of=date(2026, 7, 20))
    start_years = [int(entry.split("-")[0]) for entry in result]
    assert start_years == sorted(start_years, reverse=True)
    assert start_years == list(
        range(start_years[0], start_years[0] - FEDERAL_HISTORY_YEARS, -1)
    )


def test_federal_csv_url_contract() -> None:
    assert FEDERAL_CSV_URL.format(year="2026-2027") == (
        "https://canadabuys.canada.ca/opendata/pub/2026-2027-awardNotice-avisAttribution.csv"
    )


def test_federal_years_environment_override_remains_authoritative(monkeypatch) -> None:
    monkeypatch.setenv(
        "CONTRACT_AWARDS_FEDERAL_YEARS",
        "2024-2025, 2022-2023",
    )

    assert _federal_years(as_of=date(2026, 7, 20)) == (
        "2024-2025",
        "2022-2023",
    )


def test_empty_environment_override_falls_back_to_dynamic_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CONTRACT_AWARDS_FEDERAL_YEARS", " , ")

    assert _federal_years(as_of=date(2026, 7, 20))[0] == "2026-2027"


def test_truly_empty_string_environment_override_falls_back_to_dynamic_defaults(
    monkeypatch,
) -> None:
    """get_env returns "" verbatim (not the default arg) when the var is set
    but empty -- _federal_years must still fail-safe to the dynamic default
    rather than yielding an empty tuple of federal years."""
    monkeypatch.setenv("CONTRACT_AWARDS_FEDERAL_YEARS", "")

    assert _federal_years(as_of=date(2026, 7, 20)) == _default_federal_years(
        as_of=date(2026, 7, 20)
    )
