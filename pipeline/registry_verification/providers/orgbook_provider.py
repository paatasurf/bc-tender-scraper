"""BC OrgBook verification provider."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Company, CompanyRegistryLink
from db.registry_constants import REGISTRY_SOURCE_ORGBOOK
from pipeline.registry_verification.orgbook_import import import_orgbook_reference
from pipeline.registry_verification.orgbook_match import (
    build_orgbook_match_index,
    match_company_to_orgbook,
    match_orgbook_for_companies,
)
from pipeline.registry_verification.payload import registry_link_to_verification_payload


class OrgbookProvider:
    source = REGISTRY_SOURCE_ORGBOOK

    def import_reference_data(self, session: Session, *, path: str) -> dict[str, Any]:
        return import_orgbook_reference(session, path)

    def match_company(
        self,
        session: Session,
        company_id: int,
        *,
        include_review_tiers: bool = False,
    ) -> dict[str, Any] | None:
        del include_review_tiers
        company = session.get(Company, company_id)
        if company is None:
            return None
        index = build_orgbook_match_index(session)
        match = match_company_to_orgbook(company, index)
        if match is None:
            return None
        return {
            "source": self.source,
            "company_id": company_id,
            "external_id": match.reference.orgbook_id,
            "match_tier": match.match_tier,
            "confidence": match.confidence,
            "verification_status": match.verification_status,
        }

    def batch_match(
        self,
        session: Session,
        *,
        company_ids: list[int] | None = None,
        include_review_tiers: bool = False,
    ) -> dict[str, Any]:
        return match_orgbook_for_companies(
            session,
            company_ids=company_ids,
            include_review_tiers=include_review_tiers,
        )

    def build_profile(self, session: Session, company_id: int) -> dict[str, Any] | None:
        link = session.scalar(
            select(CompanyRegistryLink)
            .where(
                CompanyRegistryLink.company_id == company_id,
                CompanyRegistryLink.source == self.source,
            )
            .order_by(CompanyRegistryLink.confidence.desc(), CompanyRegistryLink.linked_at.desc())
        )
        if link is None:
            return None
        return registry_link_to_verification_payload(link)
