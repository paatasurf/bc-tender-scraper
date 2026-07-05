"""Source-type constants for the universal parsed_identities layer."""

from __future__ import annotations

SOURCE_TYPE_PERMIT = "permit"
SOURCE_TYPE_TENDER = "tender"
SOURCE_TYPE_COMMERCIAL_TENDER = "commercial_tender"
SOURCE_TYPE_ARCH_TENDER = "arch_tender"
SOURCE_TYPE_CONTRACT_AWARD = "contract_award"
SOURCE_TYPE_EARLY_SIGNAL = "early_signal"

SOURCE_TYPES = frozenset(
    {
        SOURCE_TYPE_PERMIT,
        SOURCE_TYPE_TENDER,
        SOURCE_TYPE_COMMERCIAL_TENDER,
        SOURCE_TYPE_ARCH_TENDER,
        SOURCE_TYPE_CONTRACT_AWARD,
        SOURCE_TYPE_EARLY_SIGNAL,
    }
)

# (source_type, field_name on ORM row)
IDENTITY_FIELD_REGISTRY: tuple[tuple[str, str], ...] = (
    (SOURCE_TYPE_PERMIT, "applicant"),
    (SOURCE_TYPE_PERMIT, "contractor"),
    (SOURCE_TYPE_TENDER, "organization"),
    (SOURCE_TYPE_COMMERCIAL_TENDER, "company"),
    (SOURCE_TYPE_ARCH_TENDER, "company"),
    (SOURCE_TYPE_CONTRACT_AWARD, "winner_company"),
    (SOURCE_TYPE_EARLY_SIGNAL, "applicant"),
)
