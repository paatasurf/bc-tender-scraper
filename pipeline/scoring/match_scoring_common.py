"""Shared utilities for deterministic match scoring engines."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pipeline.scoring.explain import BreakdownFactor

STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "ltd",
        "inc",
        "dba",
        "of",
        "for",
        "a",
        "an",
        "to",
        "in",
        "no",
        "not",
        "with",
        "by",
        "on",
        "or",
        "co",
        "corp",
        "company",
        "limited",
        "services",
        "service",
        "group",
        "bc",
        "vancouver",
    }
)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^\w\s-]", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _token_set(value: str | None, *, min_len: int = 3) -> set[str]:
    text = _normalize_text(value)
    if not text:
        return set()
    return {t for t in text.split() if len(t) >= min_len}


def tokenize_geo(text: str, *, min_len: int = 4) -> list[str]:
    """Tokenize location text without keyword stop-word filtering."""
    tokens = re.split(r"[^a-z]+", _normalize_text(text))
    return [t for t in tokens if len(t) >= min_len]


def tokenize(text: str, *, min_len: int = 3) -> list[str]:
    tokens = re.split(r"[^a-z]+", _normalize_text(text))
    return [t for t in tokens if len(t) >= min_len and t not in STOP_WORDS]


def _tokens_overlap(a: str | None, b: str | None) -> bool:
    left = _token_set(a)
    right = _token_set(b)
    if not left or not right:
        return False
    return bool(left & right)


def _parse_date(value: str | None):
    if not value:
        return None
    cleaned = str(value).replace("/", "-").strip()[:10]
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None


def _factor_to_json(factor: BreakdownFactor) -> dict[str, Any]:
    return {
        "points": factor.points,
        "max_points": factor.max_points,
        "detail": factor.detail,
    }


def assert_score_equals_breakdown(total: int, api_breakdown: dict[str, dict[str, Any]]) -> None:
    api_sum = sum(int(item.get("points", 0)) for item in api_breakdown.values())
    if api_sum != total:
        raise ValueError(f"API breakdown sum {api_sum} != total {total}")


def to_api_breakdown_seven_key(
    factors: dict[str, BreakdownFactor],
    *,
    key_order: tuple[str, ...],
    default_detail: str = "No significant signal",
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in key_order:
        factor = factors.get(key)
        if factor is None:
            result[key] = {"points": 0, "detail": default_detail}
        else:
            result[key] = {"points": factor.points, "detail": factor.detail}
    return result


def breakdown_json_to_api_breakdown_generic(
    stored: dict[str, Any] | None,
    *,
    key_order: tuple[str, ...],
    default_detail: str = "No significant signal",
) -> dict[str, dict[str, Any]]:
    if not stored:
        return {key: {"points": 0, "detail": default_detail} for key in key_order}

    result: dict[str, dict[str, Any]] = {}
    for key in key_order:
        entry = stored.get(key)
        if isinstance(entry, dict):
            result[key] = {
                "points": int(entry.get("points", 0)),
                "detail": str(entry.get("detail", default_detail)),
            }
        else:
            result[key] = {"points": 0, "detail": default_detail}
    return result
