"""Constants for market_registry staging and shared observation lifecycle."""

from __future__ import annotations

# --- market_registry.source ---
MARKET_SOURCE_ENTERPRISE_SEED = "enterprise_seed"
MARKET_SOURCE_ODB_PRIMARY = "odb_primary"
MARKET_SOURCE_ODB_OR_CANDIDATE = "odb_or_candidate"
MARKET_SOURCE_AWARDS = "awards"
MARKET_SOURCE_PERMITS = "permits"

MARKET_SOURCES = frozenset(
    {
        MARKET_SOURCE_ENTERPRISE_SEED,
        MARKET_SOURCE_ODB_PRIMARY,
        MARKET_SOURCE_ODB_OR_CANDIDATE,
        MARKET_SOURCE_AWARDS,
        MARKET_SOURCE_PERMITS,
    }
)

# --- market_registry.feed_kind ---
FEED_FORCED_REGISTRY = "forced_registry"
FEED_CORE_REGISTRY = "core_registry"
FEED_CANDIDATE_QUEUE = "candidate_queue"
FEED_EVIDENCE_ONLY = "evidence_only"

MARKET_FEED_KINDS = frozenset(
    {
        FEED_FORCED_REGISTRY,
        FEED_CORE_REGISTRY,
        FEED_CANDIDATE_QUEUE,
        FEED_EVIDENCE_ONLY,
    }
)

# --- market_registry.promotion_status ---
PROMOTION_CORE = "core"
PROMOTION_CANDIDATE = "candidate"
PROMOTION_REJECTED = "rejected"
PROMOTION_EVIDENCE_ONLY = "evidence_only"

MARKET_PROMOTION_STATUSES = frozenset(
    {
        PROMOTION_CORE,
        PROMOTION_CANDIDATE,
        PROMOTION_REJECTED,
        PROMOTION_EVIDENCE_ONLY,
    }
)

# --- observation_status (market_registry + odbus_reference) ---
OBSERVATION_STATUS_ACTIVE = "active"
OBSERVATION_STATUS_INACTIVE = "inactive"
OBSERVATION_STATUS_SUPERSEDED = "superseded"

OBSERVATION_STATUSES = frozenset(
    {
        OBSERVATION_STATUS_ACTIVE,
        OBSERVATION_STATUS_INACTIVE,
        OBSERVATION_STATUS_SUPERSEDED,
    }
)

# --- market_registry.source_confidence (A–E) ---
CONFIDENCE_A = "A"
CONFIDENCE_B = "B"
CONFIDENCE_C = "C"
CONFIDENCE_D = "D"
CONFIDENCE_E = "E"

SOURCE_CONFIDENCE_LEVELS = frozenset(
    {CONFIDENCE_A, CONFIDENCE_B, CONFIDENCE_C, CONFIDENCE_D, CONFIDENCE_E}
)

DEFAULT_SOURCE_CONFIDENCE: dict[str, str] = {
    MARKET_SOURCE_ENTERPRISE_SEED: CONFIDENCE_A,
    MARKET_SOURCE_ODB_PRIMARY: CONFIDENCE_B,
    MARKET_SOURCE_ODB_OR_CANDIDATE: CONFIDENCE_D,
    MARKET_SOURCE_AWARDS: CONFIDENCE_B,
    MARKET_SOURCE_PERMITS: CONFIDENCE_C,
}

# --- market_registry.name_type ---
NAME_TYPE_LEGAL = "legal"
NAME_TYPE_TRADE = "trade"
NAME_TYPE_VENDOR = "vendor"
NAME_TYPE_UNKNOWN = "unknown"

NAME_TYPES = frozenset({NAME_TYPE_LEGAL, NAME_TYPE_TRADE, NAME_TYPE_VENDOR, NAME_TYPE_UNKNOWN})

# --- ODB import filters ---
ODBUS_FILTER_PRIMARY_NAICS23 = "primary_naics23"
ODBUS_FILTER_OR_NAICS23 = "or_naics23"
ODBUS_FILTER_ALL = "all"

ODBUS_FILTER_MODES = frozenset(
    {
        ODBUS_FILTER_PRIMARY_NAICS23,
        ODBUS_FILTER_OR_NAICS23,
        ODBUS_FILTER_ALL,
    }
)

PRODUCTION_AUTHORIZED_ODBUS_FILTERS = frozenset({ODBUS_FILTER_PRIMARY_NAICS23})
