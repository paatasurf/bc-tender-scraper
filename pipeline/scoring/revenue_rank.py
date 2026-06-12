"""Revenue-weighted ranking helpers."""

from __future__ import annotations

import math


def parse_estimated_value(raw: str | float | None, fallback: float = 0.0) -> float:
    if raw is None:
        return fallback
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except ValueError:
        return fallback


def revenue_weight(value: float) -> float:
    if value <= 0:
        return 0.4
    clamped = max(10_000.0, min(value, 10_000_000.0))
    return round(math.log10(clamped) / math.log10(10_000_000), 3)


def rank_key(score: int, estimated_value: float) -> float:
    rw = revenue_weight(estimated_value)
    return round(score * (0.6 + 0.4 * rw), 2)
