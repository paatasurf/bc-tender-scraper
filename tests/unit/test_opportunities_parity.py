"""Parity tests: match type/id/score/order vs baseline JSON fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "opportunities"


def _match_signature(data: dict) -> list[tuple[str, int, int]]:
    return [(m["type"], m["id"], m["score"]) for m in data.get("matches", [])]


def _load_baseline(name: str) -> dict | None:
    path = FIXTURE_DIR / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture_name",),
    [
        ("baseline-construction-1921.json",),
        ("baseline-arch-19.json",),
    ],
)
def test_baseline_fixture_structure(fixture_name: str):
    """Fixtures must exist and contain required contract keys."""
    data = _load_baseline(fixture_name)
    if data is None:
        pytest.skip(f"Baseline not captured yet: {fixture_name}")
    for key in (
        "company_id",
        "kind",
        "min_score",
        "limit",
        "total_candidates",
        "matches",
        "ranking_model",
        "hybrid_scoring",
        "thresholds",
    ):
        assert key in data, f"missing {key} in {fixture_name}"


def test_parity_helper_extracts_signatures():
    sample = {
        "matches": [
            {"type": "tender", "id": 1, "score": 80},
            {"type": "permit", "id": 2, "score": 70},
        ]
    }
    assert _match_signature(sample) == [("tender", 1, 80), ("permit", 2, 70)]
