"""Cohort type isolation — GC cohorts use an allowlist, not a blocklist."""

from __future__ import annotations

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.competitive_intel.types import CompanyRow, Kind

# Profile types that trigger GC cohort allowlist filtering.
GC_BUILDER_PROFILE_TERMS = (
    "general contractor",
    "trade contractor",
    "construction",
    "builder",
)

# GC cohort members must match at least one term in name or category fields.
GC_COHORT_ALLOWLIST_TERMS = (
    "construction",
    "contracting",
    "contractor",
    "builder",
    "builders",
    "building",
    "development",
    "developments",
    "homes",
    "renovations",
    "restoration",
    "remodeling",
)

# Name/category signals that disqualify a row even when company_type is wrong.
GC_NAME_DENY_TERMS = (
    "design studio",
    "architrix",
    "designs group",
    "interior design",
    "office environments",
    "office interiors",
    "fit-out",
    "space planning",
    "building code",
    "code consultant",
    "building envelope",
    "architect",
    "architecture",
    "engineer",
    "engineering",
    "surveyor",
    "inspection",
    "landscape",
    "consulting",
    "certified professional",
)

# company_type values that never belong in a GC competitor cohort.
NON_GC_COMPANY_TYPES = frozenset(
    {
        "architect",
        "engineering firm",
        "consultant",
        "building code consultant",
        "homeowner",
    }
)

GC_COMPANY_TYPES = frozenset({"general contractor", "trade contractor"})


def _member_classification_text(company: CompanyRow) -> str:
    parts = [
        getattr(company, "name", "") or "",
        getattr(company, "company_type", "") or "",
        getattr(company, "dominant_sector", "") or "",
        getattr(company, "primary_trade", "") or "",
    ]
    for value in getattr(company, "project_types", None) or []:
        parts.append(value or "")
    for value in getattr(company, "trade_tags", None) or []:
        parts.append(value or "")
    for value in getattr(company, "award_categories", None) or []:
        parts.append(value or "")
    return " ".join(parts).lower()


def is_gc_builder_profile(
    company: CompanyRow,
    subject_cip: CompanyIntelligenceProfile | None = None,
) -> bool:
    """True when the profile company is a GC, builder, or construction contractor."""
    texts: list[str] = [_member_classification_text(company)]
    if subject_cip is not None:
        texts.append(
            " ".join(
                filter(
                    None,
                    [
                        subject_cip.company_type,
                        subject_cip.entity_class,
                        subject_cip.primary_trade,
                        subject_cip.work_orientation,
                    ],
                )
            ).lower()
        )

    combined = " ".join(texts)
    if any(term in combined for term in GC_BUILDER_PROFILE_TERMS):
        return True

    trade = (getattr(company, "primary_trade", "") or "").strip().lower()
    if trade in {"general_building", "builder", "construction", "general_contractor"}:
        return True

    if subject_cip is not None and subject_cip.entity_class == "contractor":
        return True

    return False


def is_allowed_gc_cohort_member(member: CompanyRow) -> bool:
    """True when a companies-table row may appear in a GC/builder cohort."""
    text = _member_classification_text(member)
    if any(term in text for term in GC_NAME_DENY_TERMS):
        return False

    company_type = (getattr(member, "company_type", "") or "").strip().lower()
    if company_type in NON_GC_COMPANY_TYPES:
        return False
    if company_type in GC_COMPANY_TYPES:
        return True

    return any(term in text for term in GC_COHORT_ALLOWLIST_TERMS)


def apply_cohort_type_isolation(
    members: list[CompanyRow],
    subject: CompanyRow,
    *,
    kind: Kind,
    subject_cip: CompanyIntelligenceProfile | None = None,
) -> list[CompanyRow]:
    """Pre-filter cohort rows before quality gates and peer scoring."""
    if kind != "construction":
        return members
    if not is_gc_builder_profile(subject, subject_cip):
        return members
    return [member for member in members if is_allowed_gc_cohort_member(member)]
