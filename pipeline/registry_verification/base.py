"""Verification provider interface for the Verification Hub."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session


class VerificationProvider(Protocol):
    """Each official verification source implements this interface."""

    source: str

    def import_reference_data(self, session: Session, *, path: str) -> dict[str, Any]:
        """Load or refresh read-only reference data."""

    def match_company(
        self,
        session: Session,
        company_id: int,
        *,
        include_review_tiers: bool = False,
    ) -> dict[str, Any] | None:
        """Match one canonical company; returns link summary or None."""

    def batch_match(
        self,
        session: Session,
        *,
        company_ids: list[int] | None = None,
        include_review_tiers: bool = False,
    ) -> dict[str, Any]:
        """Match canonical companies to reference records; never creates companies."""

    def build_profile(self, session: Session, company_id: int) -> dict[str, Any] | None:
        """Return provider-specific evidence for a company profile."""


# Backward-compatible alias
RegistryVerificationProvider = VerificationProvider
