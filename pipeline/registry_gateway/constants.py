"""Registry Gateway constants (Phase 2)."""

from __future__ import annotations

DECISION_MATCH = "match"
DECISION_MERGE = "merge"
DECISION_CREATE = "create"
DECISION_REJECT = "reject"
DECISION_REVIEW = "review"

GATEWAY_MODE_LEGACY = "legacy"
GATEWAY_MODE_SHADOW = "shadow"
GATEWAY_MODE_ENFORCE = "enforce"

POLICY_VERSION_V1 = "1"

SOURCE_PATH_COMPANY_RESOLVER_CREATE = "company_resolver.create"
SOURCE_PATH_POPULATE_AWARDS = "populate_companies_from_awards"

ENV_KG_GATEWAY_SHADOW = "KG_GATEWAY_SHADOW"
ENV_KG_GATEWAY_ENFORCE = "KG_GATEWAY_ENFORCE"

REJECT_REASON_PERSON = "person_name"
REJECT_REASON_JUNK = "junk_vendor"
REJECT_REASON_GATEWAY_ENFORCE = "gateway_enforce_block"
REJECT_REASON_BYPASS_BLOCKED = "constitution_bypass_blocked"
