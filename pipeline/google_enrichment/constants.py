"""Google enrichment status vocabulary — single source of truth."""

from __future__ import annotations

pending = "pending"
enriched = "enriched"
review = "review"
no_match = "no_match"
error = "error"
stale = "stale"

GOOGLE_ENRICHMENT_STATUSES: tuple[str, ...] = (
    pending,
    enriched,
    review,
    no_match,
    error,
    stale,
)

# Eligible for automatic provider lookup (excludes manual review queue).
GOOGLE_ENRICHMENT_AUTO_LOOKUP_STATUSES: frozenset[str] = frozenset(
    {
        pending,
        enriched,
        stale,
        no_match,
        error,
    }
)

# Blocked while awaiting admin decision.
GOOGLE_ENRICHMENT_BLOCKED_STATUSES: frozenset[str] = frozenset({review})
