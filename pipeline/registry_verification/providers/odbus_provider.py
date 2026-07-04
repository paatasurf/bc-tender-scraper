"""Statistics Canada ODB verification provider."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Company, CompanyRegistryLink
from db.registry_constants import REGISTRY_SOURCE_ODBUS
from pipeline.registry_verification.odbus_import import import_odbus_csv
from pipeline.registry_verification.odbus_match import (
    build_odbus_match_index,
    match_company_to_odbus,
    match_odbus_for_companies,
)
from pipeline.registry_verification.payload import registry_link_to_verification_payload


class OdbusProvider:
    source = REGISTRY_SOURCE_ODBUS

    def import_reference_data(
        self,
        session: Session,
        *,
        path: str,
        filter_mode: str | None = None,
    ) -> dict[str, Any]:
        from db.market_registry_constants import ODBUS_FILTER_PRIMARY_NAICS23

        return import_odbus_csv(
            session,
            path,
            filter_mode=filter_mode or ODBUS_FILTER_PRIMARY_NAICS23,
        )

    def match_company(
        self,
        session: Session,
        company_id: int,
        *,
        include_review_tiers: bool = False,
    ) -> dict[str, Any] | None:
        company = session.get(Company, company_id)
        if company is None:
            return None
        index = build_odbus_match_index(session)
        match = match_company_to_odbus(company, index, include_review_tiers=include_review_tiers)
        if match is None:
            return None
        return {
            "source": self.source,
            "company_id": company_id,
            "external_id": match.reference.odbus_idx,
            "match_tier": match.match_tier,
            "confidence": match.confidence,
            "verification_status": match.verification_status,
            "metadata": match.reference.odbus_idx,
        }

    def batch_match(
        self,
        session: Session,
        *,
        company_ids: list[int] | None = None,
        include_review_tiers: bool = False,
    ) -> dict[str, Any]:
        return match_odbus_for_companies(
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
