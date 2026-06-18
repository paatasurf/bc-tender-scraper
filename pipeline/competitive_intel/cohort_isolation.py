"""Cohort type isolation — keep GC/builder peers out of consultant/architecture pools."""

from __future__ import annotations

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.competitive_intel.types import CompanyRow, Kind

# Profile types that require non-construction peers excluded from cohort.
GC_BUILDER_PROFILE_TERMS = (
    "general contractor",
    "trade contractor",
    "construction",
    "builder",
)

# Cohort members to exclude when the subject is GC/builder/construction.
NON_CONSTRUCTION_EXCLUSION_TERMS = (
    "code consultant",
    "building code",
    "building envelope",
    "designs group",
    "interior design",
    "landscape",
    "architect",
    "architecture",
    "engineer",
    "engineering",
    "surveyor",
    "inspection",
    "consulting",
)


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


def is_excluded_non_construction_member(member: CompanyRow) -> bool:
    """True when a companies-table row should not appear in a GC/builder cohort."""
    text = _member_classification_text(member)
    return any(term in text for term in NON_CONSTRUCTION_EXCLUSION_TERMS)


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
    return [member for member in members if not is_excluded_non_construction_member(member)]
