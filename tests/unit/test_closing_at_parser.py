"""Unit tests for P2-06 closing_at parser."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from db.closing_at_parser import VANCOUVER_TZ, parse_closing_at

VAN = VANCOUVER_TZ


@pytest.mark.parametrize(
    ("raw", "expected_local"),
    [
        ("2026/07/03", datetime(2026, 7, 3, 23, 59, 59, tzinfo=VAN)),
        ("2026/07/24", datetime(2026, 7, 24, 23, 59, 59, tzinfo=VAN)),
        ("2026-07-31 14:00", datetime(2026, 7, 31, 14, 0, tzinfo=VAN)),
        ("July 23, 2026, 2:00 pm", datetime(2026, 7, 23, 14, 0, tzinfo=VAN)),
        ("June 26, 2026, 2:00 pm", datetime(2026, 6, 26, 14, 0, tzinfo=VAN)),
        (
            "2026-07-06 at 15:00 pt Launch BOBS Wizard My Bid Submissions View Documents",
            datetime(2026, 7, 6, 15, 0, tzinfo=VAN),
        ),
        ("2026-07-09T18:30:00+00:00", datetime(2026, 7, 9, 11, 30, tzinfo=VAN)),
    ],
)
def test_parse_closing_at_supported_formats(raw: str, expected_local: datetime) -> None:
    parsed = parse_closing_at(raw)
    assert parsed is not None
    assert parsed == expected_local


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "Not Available",
        "N/A",
        "tbd",
        "TBA",
        "soon",
        "not-a-date",
        "2026/13/40",
        "February 30, 2026, 2:00 pm",
    ],
)
def test_parse_closing_at_garbage_or_sentinel_returns_none(raw: str | None) -> None:
    assert parse_closing_at(raw) is None


def test_parse_closing_at_date_only_uses_end_of_day_vancouver() -> None:
    parsed = parse_closing_at("2026/01/15")
    assert parsed == datetime(2026, 1, 15, 23, 59, 59, tzinfo=VAN)


def test_parse_closing_at_explicit_offset_wins_over_vancouver_default() -> None:
    parsed = parse_closing_at("2026-07-01T07:00:00+00:00")
    assert parsed == datetime(2026, 7, 1, 0, 0, tzinfo=VAN)
