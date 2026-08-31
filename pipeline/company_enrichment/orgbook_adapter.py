"""OrgBook BC provider adapter (RFC Phase 0:
docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md S4).

Thin wrapper over the existing pipeline.registry_verification.hub OrgBook
provider -- zero new matching logic. hub.match_company() decides whether a
company matches an OrgBook reference record at all;
hub.get_provider("orgbook").build_profile() returns the evidence for an already-linked company.
This adapter only reshapes that existing output into the shared
ProviderResult/ProviderFact contract (RFC Phase 0 acceptance criterion).

hub.match_company()'s own result carries identity/verification fields
(external_id, match_tier, confidence, verification_status) -- it does not
itself carry phone/street-address/website facts. hub.get_provider("orgbook").build_profile()
(via registry_link_to_verification_payload) additionally carries
legal_name, business_number, city, and province when OrgBook's reference
data has them. This adapter surfaces exactly those facts and nothing
more -- OrgBook is free, public BC-registry identity data, not a contact
directory; the website provider (RFC Phase 3, not implemented here) is
the intended source for phone/street-address facts.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from pipeline.company_enrichment.provider import (
    EnrichmentRequest,
    ProviderFact,
    ProviderResult,
)
from pipeline.registry_verification import hub

_PROFILE_FACT_FIELDS = ("legal_name", "business_number", "city", "province")


class OrgBookAdapter:
    """EnrichmentProvider implementation wrapping the OrgBook BC registry
    verification provider. is_fact_source=True: it never invents a fact,
    it only surfaces what pipeline.registry_verification already found."""

    name = "orgbook"
    is_fact_source = True

    def lookup(self, session: Session, request: EnrichmentRequest) -> ProviderResult:
        try:
            match = hub.match_company(session, request.company_id, source="orgbook")
        except Exception as exc:  # noqa: BLE001 -- provider errors are isolated per RFC S7 step 5
            return ProviderResult(provider=self.name, matched=False, error=str(exc))

        if match is None:
            return ProviderResult(provider=self.name, matched=False)

        confidence = match.get("confidence")
        profile = hub.get_provider("orgbook").build_profile(session, request.company_id) or {}

        facts = tuple(
            ProviderFact(field_name=field_name, value=profile[field_name], confidence=confidence)
            for field_name in _PROFILE_FACT_FIELDS
            if profile.get(field_name)
        )
        return ProviderResult(provider=self.name, matched=True, facts=facts)
