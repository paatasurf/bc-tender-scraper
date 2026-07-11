"""Shared fixtures for competitive intelligence unit tests."""

from __future__ import annotations

from db.models import ArchCompany, Company
from pipeline.cip_schema import CompanyIntelligenceProfile, GeoConcentration, ValueRange


def make_cip(
    *,
    company_id: int = 1,
    kind: str = "construction",
    name: str = "Test Co",
    sector_focus: dict[str, float] | None = None,
    service_cities: list[str] | None = None,
    concentration_map: list[GeoConcentration] | None = None,
    value_range: ValueRange | None = None,
    award_clients: list[str] | None = None,
) -> CompanyIntelligenceProfile:
    return CompanyIntelligenceProfile(
        version=2,
        computed_at="2026-01-01T00:00:00+00:00",
        company_id=company_id,
        kind=kind,
        name=name,
        company_type="General Contractor",
        entity_class="contractor",
        primary_trade="general_building",
        secondary_trades=[],
        trade_sources=["permits"],
        specialization_confidence=0.8,
        delivery_types=["new_build"],
        normalized_project_types=["building"],
        sector_focus=sector_focus or {"institutional": 0.6, "commercial": 0.4},
        dominant_sector="institutional",
        sector_confidence="high",
        work_orientation="construction",
        buyer_types=["municipal"],
        client_types=[],
        public_private_ratio=0.5,
        procurement_affinity="project",
        service_cities=service_cities or ["Vancouver"],
        neighborhoods=[],
        concentration_map=concentration_map
        or [GeoConcentration(geo="Vancouver", share=0.8, project_count=40)],
        geographic_reach="local",
        value_range=value_range or ValueRange(p25=200_000, median=500_000, p75=1_000_000, max=2_000_000),
        typical_project_value=500_000,
        deal_size_band="medium",
        project_clusters=[],
        own_permit_count=40,
        award_count=5,
        award_categories=["Construction"],
        award_clients=award_clients or ["City of Vancouver"],
        architect_partners=[],
        repeat_clients=[],
        growth_direction=[],
        expansion_confidence=0.3,
        profile_completeness=0.7,
        normalized_name="testco",
    )


def make_company(**overrides) -> Company:
    defaults = {
        "id": 1,
        "name": "Pacific Build Co Ltd",
        "total_projects": 40,
        "total_value": 18_000_000.0,
        "avg_project_value": 450_000.0,
        "project_types": ["Building"],
        "neighborhoods": [],
        "primary_city": "Vancouver",
        "primary_trade": "general_building",
        "dominant_sector": "institutional",
        "award_count": 5,
        "award_clients": ["City of Vancouver"],
        "ai_reliability_score": 82,
        "last_project_date": "2026-05-01",
    }
    defaults.update(overrides)
    return Company(**defaults)


def make_arch_company(**overrides) -> ArchCompany:
    defaults = {
        "id": 10,
        "name": "Design Studio Inc",
        "total_projects": 15,
        "total_value": 3_000_000.0,
        "avg_project_value": 200_000.0,
        "project_types": ["Residential"],
        "neighborhoods": [],
        "google_address": "100 Main St, Vancouver, BC",
        "website_service_areas": ["Vancouver"],
        "primary_trade": "architecture",
        "dominant_sector": "residential",
        "ai_reliability_score": 75,
        "last_project_date": "2026-04-15",
    }
    defaults.update(overrides)
    return ArchCompany(**defaults)
