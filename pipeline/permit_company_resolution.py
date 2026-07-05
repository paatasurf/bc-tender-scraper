"""Resolve permit records to canonical companies via Company Discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.orm import Session

from db.company_canonical_constants import MERGE_METHOD_CONTRACTOR
from pipeline.company_discovery import CompanyCandidate, DiscoveryResult, discover_companies
from pipeline.company_resolution import (
    RESOLUTION_STATUS_PERSON_SKIP,
    RESOLUTION_STATUS_REVIEW,
    CompanyResolution,
    CompanyResolver,
)


@dataclass(frozen=True)
class PermitCompanyResolution:
    company_id: int | None
    confidence: float | None
    method: str
    discovery: DiscoveryResult
    candidate: CompanyCandidate | None
    resolution: CompanyResolution | None
    skipped: bool = False
    skip_reason: str = ""


def _merge_method_for_candidate(candidate: CompanyCandidate) -> str:
    if candidate.source in {"contractor", "buildingcontractor", "builder", "general_contractor"}:
        return MERGE_METHOD_CONTRACTOR
    if candidate.source.startswith("description:"):
        return candidate.source.replace("description:", "desc_")
    if candidate.source.startswith("applicant"):
        return "applicant_parsed"
    return candidate.source


def resolve_permit_company(
    session: Session,
    record: Mapping[str, Any],
    *,
    source: str,
    city: str = "",
    create_if_missing: bool = True,
) -> PermitCompanyResolution:
    """Run discovery then resolve the first valid company candidate."""
    discovery = discover_companies(record)
    resolver = CompanyResolver(session)
    permit_source = f"permits:{source}"

    for candidate in discovery.ordered_candidates():
        resolution = resolver.resolve(
            candidate.resolution_name,
            source=permit_source,
            city=city or (record.get("city") or ""),
            create_if_missing=create_if_missing,
        )
        if resolution.status == RESOLUTION_STATUS_PERSON_SKIP:
            continue
        if resolution.company_id is not None:
            discovery.selected = candidate
            return PermitCompanyResolution(
                company_id=int(resolution.company_id),
                confidence=resolution.confidence,
                method=_merge_method_for_candidate(candidate),
                discovery=discovery,
                candidate=candidate,
                resolution=resolution,
            )
        if resolution.status == RESOLUTION_STATUS_REVIEW:
            continue

    return PermitCompanyResolution(
        company_id=None,
        confidence=None,
        method="",
        discovery=discovery,
        candidate=None,
        resolution=None,
        skipped=True,
        skip_reason="no_resolvable_company_candidate",
    )


def resolve_permit_company_from_row(
    session: Session,
    row: dict[str, str],
    *,
    source: str,
    create_if_missing: bool = True,
) -> PermitCompanyResolution:
    """Convenience wrapper for import dict rows."""
    return resolve_permit_company(
        session,
        row,
        source=source,
        city=row.get("city") or "",
        create_if_missing=create_if_missing,
    )
