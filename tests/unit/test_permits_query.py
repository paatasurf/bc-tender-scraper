"""Unit tests for permit city filtering helpers."""

from __future__ import annotations

import pytest

from pipeline.permits_query import normalize_permit_city


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Vancouver", "Vancouver"),
        ("vancouver", "Vancouver"),
        (" Surrey ", "Surrey"),
        ("burnaby", "Burnaby"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_permit_city(raw: str | None, expected: str | None) -> None:
    assert normalize_permit_city(raw) == expected


def test_normalize_permit_city_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported city"):
        normalize_permit_city("Richmond")
