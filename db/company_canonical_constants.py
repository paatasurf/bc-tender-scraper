"""Constants for deterministic company canonical merge."""

from __future__ import annotations

ENTITY_ROLE_CANONICAL = "canonical"
ENTITY_ROLE_APPLICANT_ALIAS = "applicant_alias"
ENTITY_ROLE_STANDALONE = "standalone"
ENTITY_ROLE_PROBABLE_PERSON = "probable_person"

ENTITY_ROLES = frozenset(
    {
        ENTITY_ROLE_CANONICAL,
        ENTITY_ROLE_APPLICANT_ALIAS,
        ENTITY_ROLE_STANDALONE,
        ENTITY_ROLE_PROBABLE_PERSON,
    }
)

# Excluded from default company list / analytics queries.
COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES = frozenset(
    {
        ENTITY_ROLE_APPLICANT_ALIAS,
        ENTITY_ROLE_PROBABLE_PERSON,
    }
)

MERGE_RUN_STATUS_PLANNED = "planned"
MERGE_RUN_STATUS_APPLIED = "applied"
MERGE_RUN_STATUS_ROLLED_BACK = "rolled_back"

MERGE_METHOD_DBA_NAME = "dba_name"
MERGE_METHOD_NORMALIZED_KEY = "normalized_key"
MERGE_METHOD_DBA_APPLICANT = "dba_applicant"
MERGE_METHOD_LEGAL_APPLICANT = "legal_applicant"
MERGE_METHOD_EXACT_APPLICANT = "exact_applicant"
MERGE_METHOD_CONTRACTOR = "contractor"
MERGE_METHOD_PARSED_IDENTITY_APPLICANT = "parsed_identity_applicant"
MERGE_METHOD_MANUAL_BRIDGE_LEDCOR = "manual_bridge_ledcor"

MERGE_TIER_PARSED_IDENTITY_SAFE = "parsed_identity_applicant_safe"
MERGE_TIER_PARSED_IDENTITY_EXCLUDED = "parsed_identity_excluded"

PARSED_IDENTITY_MAX_ROOTS_AUTO_MERGE = 100
PARSED_IDENTITY_MIN_PARSE_CONFIDENCE = 0.8

# Groups with at least one DBA member are eligible for safe auto-merge.
MERGE_TIER_SAFE_DBA = "safe_dba_auto_merge"
MERGE_TIER_EXCLUDED_PROBABLE_PERSON = "excluded_probable_person"
MERGE_TIER_EXCLUDED_REVIEW = "excluded_review_queue"

# Preserve platform anchors — canonical id must not be replaced by insert.
FORCED_CANONICAL_IDS_BY_KEY: dict[str, int] = {
    "pontem": 8638,
}

CONFIDENCE_DBA_EXPLICIT = 1.0
CONFIDENCE_NORMALIZED_KEY = 1.0
CONFIDENCE_LEGAL_ONLY = 0.95

COMPANY_CANONICAL_MERGE_COLUMNS: tuple[str, ...] = (
    "display_name",
    "entity_role",
    "canonical_company_id",
    "applicant_signatory",
    "canonical_merge_confidence",
    "canonical_merge_method",
)

PERMIT_CANONICAL_MERGE_COLUMNS: tuple[str, ...] = (
    "company_id",
    "canonical_merge_confidence",
    "canonical_merge_method",
)
