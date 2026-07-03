"""Unit tests for Verification Hub and registry verification providers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from db.company_canonical_constants import ENTITY_ROLE_CANONICAL
from db.models import Company, CompanyRegistryLink, OdbusReference, OrgbookReference
from db.registry_constants import (
    MATCH_TIER_T1,
    MATCH_TIER_T2,
    REGISTRY_SOURCE_ODBUS,
    REGISTRY_SOURCE_ORGBOOK,
    VERIFICATION_CONFIRMED_ACTIVE,
    VERIFICATION_LEVEL_MULTI_SOURCE,
    VERIFICATION_LEVEL_NONE,
    VERIFICATION_LEVEL_OFFICIAL_REGISTRY,
    VERIFICATION_LEVEL_VERIFIED,
    VERIFICATION_REVIEW_PENDING,
)
from db.registry_verification_ddl import registry_verification_migration_statements
from pipeline.registry_verification.city_normalize import normalize_city
from pipeline.registry_verification.hub import batch_match, list_provider_sources
from pipeline.registry_verification.match_common import company_normalized_name
from pipeline.registry_verification.odbus_match import OdbusMatchIndex, match_company_to_odbus
from pipeline.registry_verification.orgbook_match import OrgbookMatchIndex, match_company_to_orgbook
from pipeline.registry_verification.payload import registry_link_to_verification_payload
from pipeline.registry_verification.summary import compute_verification_summary


def test_migration_statements_include_registry_tables():
    joined = "\n".join(registry_verification_migration_statements())
    assert "odbus_reference" in joined
    assert "orgbook_reference" in joined
    assert "company_registry_links" in joined


def test_list_provider_sources():
    assert REGISTRY_SOURCE_ODBUS in list_provider_sources()
    assert REGISTRY_SOURCE_ORGBOOK in list_provider_sources()


def test_normalize_city_strips_prefixes():
    assert normalize_city("City of Vancouver") == "vancouver"
    assert normalize_city("Township of Langley") == "langley"


def test_company_normalized_name_uses_dba():
    company = Company(
        id=1,
        name="Jack Hui DBA: Pontem Group",
        display_name="Pontem Group",
        entity_role=ENTITY_ROLE_CANONICAL,
    )
    assert company_normalized_name(company) == "pontem"


def _odbus_reference(
    idx: str,
    name: str,
    city: str,
    *,
    status: str = "Active",
    province: str = "BC",
) -> OdbusReference:
    from pipeline.company_matching import normalize_vendor_name

    return OdbusReference(
        odbus_idx=idx,
        business_name=name,
        normalized_name=normalize_vendor_name(name),
        city=city,
        normalized_city=normalize_city(city),
        province=province,
        status=status,
        derived_naics="23",
        provider="City of Vancouver",
        licence_number="BL-123",
    )


def _orgbook_reference(
    orgbook_id: str,
    legal_name: str,
    city: str,
    *,
    status: str = "Active",
    business_number: str = "123456789",
    registry_id: str = "BC1234567",
) -> OrgbookReference:
    from pipeline.company_matching import normalize_vendor_name

    return OrgbookReference(
        orgbook_id=orgbook_id,
        legal_name=legal_name,
        dba_names=[],
        normalized_name=normalize_vendor_name(legal_name),
        business_number=business_number,
        registry_id=registry_id,
        entity_type="BC Company",
        status=status,
        city=city,
        normalized_city=normalize_city(city),
        province="BC",
    )


def test_t1_match_name_and_city():
    company = Company(
        id=8756,
        name="Ledcor Construction Limited",
        display_name="Ledcor Construction Limited",
        primary_city="Vancouver",
        entity_role=ENTITY_ROLE_CANONICAL,
    )
    index = OdbusMatchIndex([_odbus_reference("1", "Ledcor Construction Limited", "Vancouver")])
    match = match_company_to_odbus(company, index)
    assert match is not None
    assert match.match_tier == MATCH_TIER_T1
    assert match.confidence == 0.95
    assert match.verification_status == VERIFICATION_CONFIRMED_ACTIVE


def test_orgbook_t1_match_name_and_city():
    company = Company(
        id=8638,
        name="Pontem Group",
        display_name="Pontem Group",
        primary_city="Vancouver",
        entity_role=ENTITY_ROLE_CANONICAL,
    )
    index = OrgbookMatchIndex([_orgbook_reference("ob-1", "Pontem Group", "Vancouver")])
    match = match_company_to_orgbook(company, index)
    assert match is not None
    assert match.match_tier == MATCH_TIER_T1
    assert match.confidence == 0.95


def test_orgbook_t2_match_name_single_city():
    company = Company(
        id=2,
        name="Example Builder Ltd",
        display_name="Example Builder Ltd",
        entity_role=ENTITY_ROLE_CANONICAL,
    )
    index = OrgbookMatchIndex([_orgbook_reference("ob-2", "Example Builder Ltd", "Burnaby")])
    match = match_company_to_orgbook(company, index)
    assert match is not None
    assert match.match_tier == MATCH_TIER_T2


def test_registry_link_payload_shape():
    link = CompanyRegistryLink(
        company_id=1,
        source=REGISTRY_SOURCE_ORGBOOK,
        external_id="abc",
        match_tier=MATCH_TIER_T1,
        confidence=0.95,
        verification_status=VERIFICATION_CONFIRMED_ACTIVE,
        metadata_json={
            "business_name": "Pontem Group",
            "legal_name": "Pontem Group Ltd",
            "status": "Active",
            "business_number": "123456789",
            "registry_id": "BC1234567",
            "entity_type": "BC Company",
            "provider": "BC OrgBook",
            "city": "Vancouver",
            "province": "BC",
        },
        linked_at=datetime.now(timezone.utc),
    )
    payload = registry_link_to_verification_payload(link)
    assert payload["verified"] is True
    assert payload["source"] == "BC OrgBook"
    assert payload["business_number"] == "123456789"
    assert payload["registry_id"] == "BC1234567"


def test_verification_summary_none():
    summary = compute_verification_summary([])
    assert summary["verification_level"] == VERIFICATION_LEVEL_NONE
    assert summary["verified_sources"] == []


def test_verification_summary_odbus_only():
    link = CompanyRegistryLink(
        company_id=1,
        source=REGISTRY_SOURCE_ODBUS,
        external_id="1",
        match_tier=MATCH_TIER_T1,
        confidence=0.95,
        verification_status=VERIFICATION_CONFIRMED_ACTIVE,
        metadata_json={},
        linked_at=datetime.now(timezone.utc),
    )
    summary = compute_verification_summary([link])
    assert summary["verification_level"] == VERIFICATION_LEVEL_VERIFIED
    assert summary["verified_sources"] == [REGISTRY_SOURCE_ODBUS]


def test_verification_summary_official_registry():
    link = CompanyRegistryLink(
        company_id=1,
        source=REGISTRY_SOURCE_ORGBOOK,
        external_id="ob-1",
        match_tier=MATCH_TIER_T1,
        confidence=0.95,
        verification_status=VERIFICATION_CONFIRMED_ACTIVE,
        metadata_json={},
        linked_at=datetime.now(timezone.utc),
    )
    summary = compute_verification_summary([link])
    assert summary["verification_level"] == VERIFICATION_LEVEL_OFFICIAL_REGISTRY


def test_verification_summary_multi_source():
    now = datetime.now(timezone.utc)
    links = [
        CompanyRegistryLink(
            company_id=1,
            source=REGISTRY_SOURCE_ODBUS,
            external_id="1",
            match_tier=MATCH_TIER_T1,
            confidence=0.95,
            verification_status=VERIFICATION_CONFIRMED_ACTIVE,
            metadata_json={},
            linked_at=now,
        ),
        CompanyRegistryLink(
            company_id=1,
            source=REGISTRY_SOURCE_ORGBOOK,
            external_id="ob-1",
            match_tier=MATCH_TIER_T1,
            confidence=0.95,
            verification_status=VERIFICATION_CONFIRMED_ACTIVE,
            metadata_json={},
            linked_at=now,
        ),
    ]
    summary = compute_verification_summary(links)
    assert summary["verification_level"] == VERIFICATION_LEVEL_MULTI_SOURCE
    assert set(summary["verified_sources"]) == {REGISTRY_SOURCE_ODBUS, REGISTRY_SOURCE_ORGBOOK}


def test_t4_only_when_review_enabled():
    company = Company(
        id=4,
        name="Alpine Construction Group",
        display_name="Alpine Construction Group",
        entity_role=ENTITY_ROLE_CANONICAL,
    )
    index = OdbusMatchIndex([_odbus_reference("4", "Alpine Construction Services", "Vancouver")])
    assert match_company_to_odbus(company, index, include_review_tiers=False) is None
    match = match_company_to_odbus(company, index, include_review_tiers=True)
    assert match is not None
    assert match.verification_status == VERIFICATION_REVIEW_PENDING


@pytest.fixture(scope="module")
def odb_csv_path(tmp_path_factory) -> Path:
    database_url = __import__("os").environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    path = tmp_path_factory.mktemp("odbus") / "sample.csv"
    path.write_text(
        "idx,business_name,alt_business_name,city,prov_terr,status,derived_NAICS,source_NAICS_primary,licence_number,business_id_no,provider,latitude,longitude\n"
        't1-1,Pontem Group,,Vancouver,BC,Active,23,236220,BL-1,,City of Vancouver,49.2,-123.1\n',
        encoding="utf-8",
    )
    return path


def test_import_and_match_integration(odb_csv_path):
    import uuid

    from db.connection import get_session, init_db
    from pipeline.registry_verification.odbus_import import import_odbus_csv
    from pipeline.registry_verification.hub import batch_match

    init_db()
    session = get_session()
    suffix = uuid.uuid4().hex[:8]
    try:
        import_odbus_csv(session, odb_csv_path)
        company = Company(
            name=f"ODB Test Canonical {suffix}",
            display_name="Pontem Group",
            primary_city="Vancouver",
            entity_role=ENTITY_ROLE_CANONICAL,
        )
        session.add(company)
        session.commit()

        results = batch_match(session, sources=[REGISTRY_SOURCE_ODBUS], company_ids=[company.id])
        assert results["providers"][REGISTRY_SOURCE_ODBUS]["matched"] == 1
        assert results["providers"][REGISTRY_SOURCE_ODBUS]["by_tier"]["T1"] == 1
    finally:
        session.close()
