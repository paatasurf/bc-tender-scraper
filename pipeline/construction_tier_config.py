"""Configuration for the Construction Tier Engine.

Tune weights and thresholds here — the scoring algorithm reads these values only.
"""

from __future__ import annotations

# Component weights (must sum to 100)
PERMIT_WEIGHT = 40
AWARD_WEIGHT = 25
TENDER_WEIGHT = 15
GEOGRAPHY_WEIGHT = 10
LONGEVITY_WEIGHT = 10

CONSTRUCTION_SCORE_MIN = 0
CONSTRUCTION_SCORE_MAX = 100

# Recency windows (months) for tier interpretation
RECENCY_MONTHS_TIER_A = 24
RECENCY_MONTHS_TIER_B = 36
RECENCY_MONTHS_TIER_C = 48
RECENCY_MONTHS_INACTIVE = 60

# Score thresholds for tier assignment (tier is derived from score + gates)
SCORE_TIER_A = 70
SCORE_TIER_B = 45
SCORE_TIER_C = 25
SCORE_TIER_D = 8

# Enterprise activity gates (tier A promotion)
TIER_A_MIN_PROJECTS = 25
TIER_A_MIN_VALUE = 10_000_000.0
TIER_A_MIN_AWARDS = 2

# Engine runtime
CONSTRUCTION_TIER_VERSION = 1
BATCH_SIZE = 500
PERMIT_STATS_MONTHS = 24
TENDER_KIND_CONSTRUCTION = "construction"

# Geographic presence source (see pipeline/construction_geography.py)
GEOGRAPHIC_PRESENCE_SOURCE = "neighborhoods"
