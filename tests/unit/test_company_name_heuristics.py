"""Unit tests for company/person name heuristics."""

from __future__ import annotations

import pytest

from pipeline.company_name_heuristics import is_probable_person_name


@pytest.mark.parametrize(
    "display_name, expected",
    [
        # Plain person names
        ("John Smith", True),
        ("Jane Marie Doe", True),
        ("Mary-Kate Olsen", True),
        ("D'Angelo Pierce", True),
        # Parenthetical nicknames (Issue 1)
        ("Yi Chieh (Ashanti) Lee", True),
        ("Yi Chieh Lee", True),
        ("John (Jack) Smith", True),
        # Not persons
        ("", False),
        ("Smith", False),
        ("ABC Construction", False),
        ("John Smith Ltd", False),
        ("John & Jane Smith", False),
        ("Smith, John", False),
        ("123 Homes", False),
        ("Yi Chieh (Ashanti) Lee Construction", False),
        ("Yi Chieh Lee Homes", False),
        # Multi-word parentheticals are intentionally not classified as nicknames
        ("Yi Chieh (Ashanti Maria) Lee", False),
    ],
)
def test_is_probable_person_name(display_name: str, expected: bool) -> None:
    assert is_probable_person_name(display_name) is expected
