"""Constants for the deterministic Construction Tier Engine."""

from __future__ import annotations

TIER_A = "tier_a"
TIER_B = "tier_b"
TIER_C = "tier_c"
TIER_D = "tier_d"
TIER_E = "tier_e"

ALL_CONSTRUCTION_TIERS = (TIER_A, TIER_B, TIER_C, TIER_D, TIER_E)

TIER_LABELS: dict[str, str] = {
    TIER_A: "Enterprise Contractors",
    TIER_B: "Established Contractors",
    TIER_C: "Growing Contractors",
    TIER_D: "Limited Activity",
    TIER_E: "Inactive / Historical",
}

DEFAULT_PLATFORM_TIERS = frozenset({TIER_A, TIER_B})

TIER_LETTER_MAP: dict[str, str] = {
    "A": TIER_A,
    "B": TIER_B,
    "C": TIER_C,
    "D": TIER_D,
    "E": TIER_E,
}

