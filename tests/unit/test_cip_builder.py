"""Unit tests for pipeline.cip_builder (PR-E3B).

Mock-based, deterministic -- never touches a database. DB-facing helpers
(_load_company_permits, _load_architect_partners, session.get/.commit)
are monkeypatched or replaced with SimpleNamespace fixtures / MagicMock,
following the same convention already established in
tests/unit/test_cip_builder_permits.py (which this file does not modify).
Golden values below were captured by executing the real, unedited
pipeline/cip_builder.py against fixed representative inputs. Read-only
against pipeline/cip_builder.py -- production logic is never modified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pipeline.cip_builder as cb
from pipeline.capability_profile import CCP_VERSION, PROFILE_TTL_HOURS
from pipeline.cip_schema import (
    CIP_VERSION,
    CompanyIntelligenceProfile,
    GeoConcentration,
    ProjectCluster,
    ValueRange,
)

# ===================================================================
# Fixture builders
# ===================================================================


def _make_permit(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=1,
        permit_type="",
        description="",
        address="",
        applicant="",
        architect="",
        project_value="",
        company_id=1,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_company(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=1,
        name="Acme Construction Ltd",
        display_name="",
        company_type="General Contractor",
        project_types=["Commercial"],
        award_categories=["Construction"],
        canonical_vendor_name="",
        award_count=3,
        award_clients=["City of Vancouver"],
        buyer_levels=["municipal"],
        award_sources=[],
        total_projects=10,
        neighborhoods=["Mount Pleasant"],
        google_address="456 Oak St, Vancouver, BC",
        primary_city="Vancouver",
        avg_award_value=400_000.0,
        avg_project_value=450_000.0,
        cip_at=None,
        cip_json=None,
        cip_version=None,
        primary_trade="",
        trade_tags=[],
        capability_profile_json=None,
        capability_profile_at=None,
        dominant_sector="",
        sector_confidence="",
        work_orientation="",
        specialization_confidence=None,
        geographic_reach="",
        value_p25=None,
        value_p75=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_arch_company(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=10,
        name="Studio Design Inc",
        display_name="",
        website_specializations=["Residential", "Sustainable Design"],
        project_types=["Residential"],
        houzz_project_types=["Home Renovation"],
        neighborhoods=["Kitsilano"],
        google_address="789 Pine St, Vancouver, BC",
        houzz_service_areas=["Vancouver", "Burnaby"],
        avg_project_value=300_000.0,
        cip_at=None,
        cip_json=None,
        cip_version=None,
        primary_trade="",
        trade_tags=[],
        capability_profile_json=None,
        capability_profile_at=None,
        dominant_sector="",
        work_orientation="",
        specialization_confidence=None,
        geographic_reach="",
        value_p25=None,
        value_p75=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_cip(**overrides) -> CompanyIntelligenceProfile:
    defaults = dict(
        version=CIP_VERSION,
        computed_at="2026-01-01T00:00:00+00:00",
        company_id=1,
        kind="construction",
        name="Test Co",
        company_type="General Contractor",
        entity_class="contractor",
        primary_trade="concrete",
        secondary_trades=["structural"],
        trade_sources=["permits"],
        specialization_confidence=0.8,
        delivery_types=["new_build"],
        normalized_project_types=["building"],
        sector_focus={"institutional": 0.6},
        dominant_sector="institutional",
        sector_confidence="high",
        work_orientation="construction",
        buyer_types=["municipal"],
        client_types=["City of Vancouver"],
        public_private_ratio=0.5,
        procurement_affinity="project",
        service_cities=["Vancouver"],
        neighborhoods=[],
        concentration_map=[
            GeoConcentration(geo="Vancouver", share=0.8, project_count=40)
        ],
        geographic_reach="local",
        value_range=ValueRange(
            p25=200_000, median=500_000, p75=1_000_000, max=2_000_000
        ),
        typical_project_value=500_000,
        deal_size_band="medium",
        project_clusters=[
            ProjectCluster(
                delivery="new_build",
                sector="institutional",
                geo="Vancouver",
                count=3,
                share=1.0,
                avg_value=500_000.0,
            )
        ],
        own_permit_count=40,
        award_count=5,
        award_categories=["Construction"],
        award_clients=["City of Vancouver"],
        architect_partners=[{"name": "Acme Architects", "project_count": 3}],
        repeat_clients=["City of Vancouver"],
        growth_direction=["commercial_expansion"],
        expansion_confidence=0.3,
        profile_completeness=0.7,
        normalized_name="testco",
        market_segments=["municipal"],
        specializations=["Construction"],
    )
    defaults.update(overrides)
    return CompanyIntelligenceProfile(**defaults)


def _patch_permit_loaders(monkeypatch, permits=None, architect_partners=None):
    monkeypatch.setattr(cb, "_load_company_permits", lambda *a, **kw: permits or [])
    monkeypatch.setattr(
        cb, "_load_architect_partners", lambda *a, **kw: architect_partners or []
    )


# ===================================================================
# _resolve_trades()
# ===================================================================


def test_resolve_trades_name_derived():
    primary, secondary, sources, confidence = cb._resolve_trades(
        name="ABC Electrical Ltd",
        company_type="",
        project_types=[],
        award_categories=[],
        specializations=[],
        permit_texts=[],
    )
    assert primary == "electrical"
    assert sources == ["company_type", "name"]
    assert confidence == 0.68


def test_resolve_trades_company_type_and_project_type_signal():
    primary, secondary, sources, confidence = cb._resolve_trades(
        name="Generic Co",
        company_type="Roofing Contractor",
        project_types=["Roofing"],
        award_categories=[],
        specializations=[],
        permit_texts=[],
    )
    assert primary == "roofing"
    assert sources == ["company_type"]
    assert confidence == 0.53


def test_resolve_trades_permit_signal():
    primary, secondary, sources, confidence = cb._resolve_trades(
        name="Generic Co",
        company_type="",
        project_types=[],
        award_categories=[],
        specializations=[],
        permit_texts=["hvac ventilation upgrade", "hvac heating system"],
    )
    assert primary == "general_building"
    assert secondary == ["hvac"]
    assert sources == ["company_type", "permits"]
    assert confidence == 0.61


def test_resolve_trades_award_signal():
    primary, secondary, sources, confidence = cb._resolve_trades(
        name="Generic Co",
        company_type="",
        project_types=[],
        award_categories=["Demolition Services"],
        specializations=[],
        permit_texts=[],
    )
    assert primary == "demolition"
    assert sources == ["awards", "company_type"]
    assert confidence == 0.53


def test_resolve_trades_primary_secondary_ordering():
    """With all four signal types combined, the highest-weighted trade
    (name, weight 5, plus its own company_type contribution) must be
    primary, with the rest ordered by descending count."""
    primary, secondary, sources, confidence = cb._resolve_trades(
        name="ABC Electrical Ltd",
        company_type="Roofing Contractor",
        project_types=["Roofing"],
        award_categories=["Demolition Services"],
        specializations=[],
        permit_texts=["hvac ventilation upgrade"],
    )
    assert primary == "electrical"
    assert secondary == ["demolition", "roofing", "hvac"]
    assert confidence == 0.92


def test_resolve_trades_sources_are_unique_and_sorted():
    _, _, sources, _ = cb._resolve_trades(
        name="ABC Electrical Ltd",
        company_type="Roofing Contractor",
        project_types=["Roofing"],
        award_categories=["Demolition Services"],
        specializations=[],
        permit_texts=["hvac ventilation upgrade"],
    )
    assert sources == sorted(set(sources))
    assert len(sources) == len(set(sources))


def test_resolve_trades_fallback_when_no_signals():
    """With every input empty, tag_company()'s own unconditional fallback
    ('general_building' via COMPANY_TYPE_TRADE.get(..., 'general_building'))
    contributes a count *before* _resolve_trades' own `if not counts`
    check ever runs -- so that branch is unreachable given today's
    tag_company() behavior, and the actual output is the company_type-
    sourced 'general_building' at confidence 0.53, not the theoretical
    0.35 the dead branch would return. Verified by execution, not
    assumption."""
    primary, secondary, sources, confidence = cb._resolve_trades(
        name="",
        company_type="",
        project_types=[],
        award_categories=[],
        specializations=[],
        permit_texts=[],
    )
    assert primary == "general_building"
    assert sources == ["company_type"]
    assert confidence == 0.53


def test_resolve_trades_confidence_capped_at_095():
    """Stack every possible confidence contributor (many distinct trade
    sources + name bonus) and confirm the result never exceeds 0.95."""
    _, _, _, confidence = cb._resolve_trades(
        name="ABC Electrical Ltd",
        company_type="Roofing Contractor",
        project_types=["Roofing"],
        award_categories=[
            "Demolition Services",
            "Landscaping Contract",
            "Civil Infrastructure",
        ],
        specializations=[],
        permit_texts=[
            "hvac ventilation",
            "concrete foundation",
            "structural steel",
            "excavation grading",
            "glazing curtain wall",
        ],
    )
    assert confidence <= 0.95


def test_resolve_trades_deterministic():
    kwargs = dict(
        name="ABC Electrical Ltd",
        company_type="Roofing Contractor",
        project_types=["Roofing"],
        award_categories=["Demolition Services"],
        specializations=[],
        permit_texts=["hvac ventilation upgrade"],
    )
    r1 = cb._resolve_trades(**kwargs)
    r2 = cb._resolve_trades(**kwargs)
    assert r1 == r2


# ===================================================================
# _build_clusters()
# ===================================================================


def test_build_clusters_groups_by_delivery_sector_geo():
    permits = [
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="100 Main St, Vancouver, BC",
            project_value="1000000",
        ),
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="200 Main St, Vancouver, BC",
            project_value="2000000",
        ),
        _make_permit(
            permit_type="Renovation",
            description="renovation residential home",
            address="50 Oak St, Burnaby, BC",
            project_value="500000",
        ),
    ]
    clusters = cb._build_clusters(permits)
    keys = {(c.delivery, c.sector, c.geo) for c in clusters}
    assert ("new_build", "commercial", "Vancouver") in keys
    assert ("renovation", "residential", "Burnaby") in keys


def test_build_clusters_count_and_share():
    permits = [
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="100 Main St, Vancouver, BC",
            project_value="1000000",
        ),
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="200 Main St, Vancouver, BC",
            project_value="2000000",
        ),
        _make_permit(
            permit_type="Renovation",
            description="renovation residential home",
            address="50 Oak St, Burnaby, BC",
            project_value="500000",
        ),
        _make_permit(
            permit_type="Renovation",
            description="renovation residential home",
            address="60 Oak St, Burnaby, BC",
            project_value="not-a-number",
        ),
        _make_permit(
            permit_type="Demolition",
            description="demolition site",
            address="",
            project_value="",
        ),
    ]
    clusters = cb._build_clusters(permits)
    by_key = {(c.delivery, c.sector, c.geo): c for c in clusters}
    commercial = by_key[("new_build", "commercial", "Vancouver")]
    assert commercial.count == 2
    assert commercial.share == 0.4  # 2/5


def test_build_clusters_average_value():
    permits = [
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="100 Main St, Vancouver, BC",
            project_value="1000000",
        ),
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="200 Main St, Vancouver, BC",
            project_value="2000000",
        ),
    ]
    clusters = cb._build_clusters(permits)
    commercial = next(c for c in clusters if c.sector == "commercial")
    assert commercial.avg_value == 1_500_000.0  # (1_000_000 + 2_000_000) / 2


def test_build_clusters_ignores_invalid_and_empty_values():
    """A non-numeric project_value must not contribute to avg_value, and
    an empty address must fall back to geo='unknown' rather than
    erroring."""
    permits = [
        _make_permit(
            permit_type="Renovation",
            description="renovation residential home",
            address="50 Oak St, Burnaby, BC",
            project_value="500000",
        ),
        _make_permit(
            permit_type="Renovation",
            description="renovation residential home",
            address="60 Oak St, Burnaby, BC",
            project_value="not-a-number",
        ),
        _make_permit(
            permit_type="Demolition",
            description="demolition site",
            address="",
            project_value="",
        ),
    ]
    clusters = cb._build_clusters(permits)
    residential = next(c for c in clusters if c.sector == "residential")
    assert residential.count == 2
    assert residential.avg_value == 500_000.0  # only the valid value counted

    demolition = next(c for c in clusters if c.delivery == "demolition")
    assert demolition.geo == "unknown"
    assert demolition.avg_value == 0.0


def test_build_clusters_empty_permits_returns_empty_list():
    assert cb._build_clusters([]) == []


def test_build_clusters_ordering_by_count_descending():
    permits = [
        _make_permit(
            permit_type="New Building",
            description="new build commercial tower",
            address="1 St, Vancouver, BC",
            project_value="1000000",
        )
        for _ in range(3)
    ] + [
        _make_permit(
            permit_type="Renovation",
            description="renovation residential home",
            address="2 St, Burnaby, BC",
            project_value="500000",
        )
        for _ in range(1)
    ]
    clusters = cb._build_clusters(permits)
    assert clusters[0].count >= clusters[-1].count
    assert clusters[0].sector == "commercial"


def test_build_clusters_respects_limit():
    import itertools

    sectors = [
        "residential home",
        "commercial tower",
        "institutional school",
        "industrial warehouse",
    ]
    deliveries = ["new build", "renovation", "demolition site"]
    cities = ["Vancouver, BC", "Burnaby, BC", "Richmond, BC", "Surrey, BC"]
    varied = []
    i = 0
    for d, s, c in itertools.product(deliveries, sectors, cities):
        varied.append(
            _make_permit(
                permit_type=d,
                description=f"{d} {s}",
                address=f"{i} St, {c}",
                project_value="100000",
            )
        )
        i += 1
    result = cb._build_clusters(varied, limit=8)
    assert len(result) == 8


# ===================================================================
# build_cip()
# ===================================================================


def test_build_cip_construction_path_golden(monkeypatch):
    permits = [
        _make_permit(
            id=i,
            permit_type="Building Permit",
            description="New concrete foundation and structural work for commercial building",
            address="123 Main St, Vancouver, BC",
            applicant="Acme Construction Ltd",
            project_value="500000",
        )
        for i in range(1, 4)
    ]
    _patch_permit_loaders(monkeypatch, permits=permits)
    session = MagicMock()
    session.get.return_value = _make_company()

    cip = cb.build_cip(session, company_id=1, kind="construction")

    assert cip.kind == "construction"
    assert cip.version == CIP_VERSION
    assert cip.company_id == 1
    assert cip.primary_trade == "general_building"
    assert cip.secondary_trades == ["concrete", "structural"]
    assert cip.dominant_sector == "commercial"
    assert cip.sector_focus == {"commercial": 1.0}
    assert cip.sector_confidence == "high"
    assert cip.project_clusters == [
        ProjectCluster(
            delivery="new_build",
            sector="commercial",
            geo="Vancouver",
            count=3,
            share=1.0,
            avg_value=500_000.0,
        )
    ]
    assert cip.value_range == ValueRange(
        p25=450_000.0, median=500_000.0, p75=500_000.0, max=500_000.0
    )
    assert cip.typical_project_value == 500_000.0
    assert cip.geographic_reach == "local"
    assert cip.profile_completeness == 1.0


def test_build_cip_architecture_path_golden(monkeypatch):
    _patch_permit_loaders(monkeypatch)
    session = MagicMock()
    session.get.return_value = _make_arch_company()

    cip = cb.build_cip(session, company_id=10, kind="architecture")

    assert cip.kind == "architecture"
    assert cip.version == CIP_VERSION
    assert cip.company_id == 10
    assert cip.entity_class == "designer"
    assert cip.company_type == "Architect"
    assert cip.primary_trade == "architecture"
    assert cip.dominant_sector == "residential"
    assert cip.sector_focus == {"residential": 1.0}
    assert cip.work_orientation == "design"
    assert cip.project_clusters == []  # architecture path never builds clusters
    assert cip.own_permit_count == 0
    assert cip.award_count == 0


def test_build_cip_missing_construction_company_raises_value_error():
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(ValueError, match="Company 999 not found"):
        cb.build_cip(session, company_id=999, kind="construction")


def test_build_cip_missing_architecture_company_raises_value_error():
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(ValueError, match="Architecture company 999 not found"):
        cb.build_cip(session, company_id=999, kind="architecture")


def test_build_cip_permit_derived_sector_geography_value_fields(monkeypatch):
    permits = [
        _make_permit(
            id=i,
            permit_type="Building Permit",
            description="New concrete foundation and structural work for commercial building",
            address="123 Main St, Vancouver, BC",
            project_value="500000",
        )
        for i in range(1, 4)
    ]
    _patch_permit_loaders(monkeypatch, permits=permits)
    session = MagicMock()
    session.get.return_value = _make_company()

    cip = cb.build_cip(session, company_id=1, kind="construction")

    # sector: derived from permit inference (all 3 permits -> commercial)
    assert cip.sector_focus == {"commercial": 1.0}
    # geography: permit addresses feed concentration_map (Vancouver x3)
    vancouver = next(g for g in cip.concentration_map if g.geo == "Vancouver")
    assert vancouver.project_count == 4  # 3 from permits + 1 from service_cities
    # value: permit project_value feeds value_range
    assert cip.value_range.median == 500_000.0


def test_build_cip_award_derived_fallback_when_no_permits(monkeypatch):
    """With zero permits, sector_focus/value/clusters must fall back to
    award_categories/company award fields instead of erroring or being
    left empty."""
    _patch_permit_loaders(monkeypatch, permits=[])
    company = _make_company(
        id=2,
        name="Fallback Awards Co",
        company_type="",
        project_types=[],
        award_categories=["Institutional Construction"],
        award_count=8,
        award_clients=["Province of BC"],
        buyer_levels=["provincial"],
        total_projects=0,
        neighborhoods=[],
        google_address="",
        primary_city="",
        avg_award_value=800_000.0,
        avg_project_value=0.0,
    )
    session = MagicMock()
    session.get.return_value = company

    cip = cb.build_cip(session, company_id=2, kind="construction")

    assert cip.project_clusters == []
    assert cip.sector_focus == {"institutional": 1.0}
    assert cip.dominant_sector == "institutional"
    assert cip.value_range.median == 800_000.0
    assert cip.typical_project_value == 800_000.0


def test_build_cip_dominant_sector_and_growth_direction(monkeypatch):
    """A secondary sector at >=12% share must appear in growth_direction
    as '<sector>_expansion'; the dominant sector itself must not."""
    permits = [
        _make_permit(
            id=i,
            permit_type="New Building",
            description="new build commercial tower",
            address="1 Main St, Vancouver, BC",
            project_value="500000",
        )
        for i in range(1, 4)
    ] + [
        _make_permit(
            id=i,
            permit_type="New Building",
            description="new build institutional school",
            address="2 Main St, Vancouver, BC",
            project_value="700000",
        )
        for i in range(4, 6)
    ]
    _patch_permit_loaders(monkeypatch, permits=permits)
    session = MagicMock()
    session.get.return_value = _make_company(
        id=3,
        name="Multi Sector Builders",
        project_types=[],
        award_categories=[],
        award_count=2,
        award_clients=[],
        buyer_levels=["federal", "municipal"],
        avg_award_value=0.0,
        avg_project_value=600_000.0,
    )

    cip = cb.build_cip(session, company_id=3, kind="construction")

    assert cip.sector_focus == {"commercial": 0.6, "institutional": 0.4}
    assert cip.dominant_sector == "commercial"
    assert "institutional_expansion" in cip.growth_direction
    assert "commercial_expansion" not in cip.growth_direction
    assert cip.expansion_confidence == 0.3


def test_build_cip_market_segments_from_buyer_levels_and_award_sources(monkeypatch):
    _patch_permit_loaders(monkeypatch)
    session = MagicMock()
    session.get.return_value = _make_company(
        buyer_levels=["federal", "municipal"], award_sources=[]
    )
    cip = cb.build_cip(session, company_id=1, kind="construction")
    assert cip.market_segments == ["federal", "municipal"]


def test_build_cip_profile_completeness_reflects_signal_richness(monkeypatch):
    """A minimally-populated company (no project types, no neighborhoods,
    no award categories, no specializations, zero avg value) must score
    a lower profile_completeness than a richly-populated one."""
    _patch_permit_loaders(monkeypatch)
    sparse_company = _make_company(
        project_types=[],
        neighborhoods=[],
        award_categories=[],
        avg_project_value=0.0,
        avg_award_value=0.0,
        company_type="",
    )
    session = MagicMock()
    session.get.return_value = sparse_company
    sparse_cip = cb.build_cip(session, company_id=1, kind="construction")

    rich_session = MagicMock()
    rich_session.get.return_value = _make_company()
    rich_cip = cb.build_cip(rich_session, company_id=1, kind="construction")

    assert sparse_cip.profile_completeness < rich_cip.profile_completeness


def test_build_cip_kind_version_company_id_are_correct(monkeypatch):
    _patch_permit_loaders(monkeypatch)
    session = MagicMock()
    session.get.return_value = _make_company()
    cip = cb.build_cip(session, company_id=42, kind="construction")
    assert cip.kind == "construction"
    assert cip.version == CIP_VERSION
    assert cip.company_id == 42


def test_build_cip_computed_at_is_a_valid_recent_timestamp_not_a_fixed_literal(
    monkeypatch,
):
    """Only assert the timestamp parses and is close to "now" -- never
    pin an exact wall-clock value, since that would make the test flaky
    by construction."""
    _patch_permit_loaders(monkeypatch)
    session = MagicMock()
    session.get.return_value = _make_company()
    before = datetime.now(timezone.utc)
    cip = cb.build_cip(session, company_id=1, kind="construction")
    after = datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(cip.computed_at)
    assert before - timedelta(seconds=5) <= parsed <= after + timedelta(seconds=5)


# ===================================================================
# cip_to_capability_profile()
# ===================================================================


def test_cip_to_capability_profile_maps_key_fields():
    cip = _make_cip()
    profile = cb.cip_to_capability_profile(cip)
    assert profile.version == CCP_VERSION
    assert profile.computed_at == cip.computed_at
    assert profile.company_id == cip.company_id
    assert profile.kind == cip.kind
    assert profile.name == cip.name
    assert profile.company_type == cip.company_type
    assert profile.primary_trade == cip.primary_trade
    assert profile.profile_completeness == cip.profile_completeness
    assert profile.normalized_name == cip.normalized_name


def test_cip_to_capability_profile_primary_secondary_trades():
    cip = _make_cip(primary_trade="concrete", secondary_trades=["structural", "civil"])
    profile = cb.cip_to_capability_profile(cip)
    assert profile.trade_tags == ["concrete", "structural", "civil"]
    assert profile.trade_confidence == cip.specialization_confidence


def test_cip_to_capability_profile_values_awards_buyers_geography():
    cip = _make_cip(
        value_range=ValueRange(p25=100_000, median=250_000, p75=400_000, max=600_000),
        typical_project_value=250_000,
        award_count=7,
        award_categories=["Roofing"],
        award_clients=["City X"],
        buyer_types=["municipal", "provincial"],
        service_cities=["Vancouver", "Burnaby"],
        neighborhoods=["Kits"],
        own_permit_count=12,
    )
    profile = cb.cip_to_capability_profile(cip)
    assert profile.avg_project_value == 250_000
    assert profile.avg_award_value == 250_000  # value_range.median takes priority
    assert profile.award_count == 7
    assert profile.award_categories == ["Roofing"]
    assert profile.award_clients == ["City X"]
    assert profile.buyer_levels == ["municipal", "provincial"]
    assert profile.service_cities == ["Vancouver", "Burnaby"]
    assert profile.neighborhoods == ["Kits"]
    assert profile.own_permit_count == 12


def test_cip_to_capability_profile_avg_award_value_falls_back_to_typical_when_median_zero():
    cip = _make_cip(
        value_range=ValueRange(p25=0, median=0, p75=0, max=0),
        typical_project_value=333_000,
    )
    profile = cb.cip_to_capability_profile(cip)
    assert profile.avg_award_value == 333_000


def test_cip_to_capability_profile_does_not_mutate_source_cip():
    cip = _make_cip()
    before = cip.to_dict()
    cb.cip_to_capability_profile(cip)
    after = cip.to_dict()
    assert before == after


def test_cip_to_capability_profile_deterministic():
    cip = _make_cip()
    p1 = cb.cip_to_capability_profile(cip)
    p2 = cb.cip_to_capability_profile(cip)
    assert p1.to_dict() == p2.to_dict()


# ===================================================================
# persist_cip()
# ===================================================================


def test_persist_cip_construction_row_fields(monkeypatch):
    mock_merge = MagicMock(return_value={"merged": True})
    monkeypatch.setattr(cb, "merge_registry_provenance_into_profile", mock_merge)
    cip = _make_cip(kind="construction")
    row = _make_company()
    session = MagicMock()
    session.get.return_value = row

    cb.persist_cip(session, cip)

    assert row.primary_trade == cip.primary_trade
    assert row.trade_tags == [cip.primary_trade, *cip.secondary_trades[:4]]
    assert row.capability_profile_json == {"merged": True}
    assert row.capability_profile_at is not None
    assert row.cip_json == cip.to_dict()
    assert row.cip_at is not None
    assert row.cip_version == cb.CIP_VERSION
    assert row.dominant_sector == cip.dominant_sector
    assert row.sector_confidence == cip.sector_confidence
    assert row.work_orientation == cip.work_orientation
    assert row.specialization_confidence == cip.specialization_confidence
    assert row.geographic_reach == cip.geographic_reach
    assert row.value_p25 == cip.value_range.p25
    assert row.value_p75 == cip.value_range.p75


def test_persist_cip_architecture_row_fields(monkeypatch):
    mock_merge = MagicMock(return_value={"merged": True})
    monkeypatch.setattr(cb, "merge_registry_provenance_into_profile", mock_merge)
    cip = _make_cip(kind="architecture", company_id=10)
    row = _make_arch_company()
    session = MagicMock()
    session.get.return_value = row

    cb.persist_cip(session, cip)

    assert row.primary_trade == cip.primary_trade
    assert row.trade_tags == [cip.primary_trade, *cip.secondary_trades[:4]]
    assert row.cip_json == cip.to_dict()
    assert row.cip_version == cb.CIP_VERSION
    assert row.dominant_sector == cip.dominant_sector
    assert row.work_orientation == cip.work_orientation
    assert row.specialization_confidence == cip.specialization_confidence
    assert row.geographic_reach == cip.geographic_reach
    assert row.value_p25 == cip.value_range.p25
    assert row.value_p75 == cip.value_range.p75
    # architecture rows never receive sector_confidence -- only the
    # construction branch sets that field.
    assert not hasattr(row, "sector_confidence") or row.sector_confidence == ""


def test_persist_cip_calls_registry_provenance_merge(monkeypatch):
    mock_merge = MagicMock(return_value={"merged": True})
    monkeypatch.setattr(cb, "merge_registry_provenance_into_profile", mock_merge)
    cip = _make_cip()
    row = _make_company(capability_profile_json={"registry_provenance": {"x": 1}})
    session = MagicMock()
    session.get.return_value = row

    cb.persist_cip(session, cip)

    mock_merge.assert_called_once()
    args, kwargs = mock_merge.call_args
    assert args[0] == cb.cip_to_capability_profile(cip).to_dict()
    assert kwargs["existing_capability_profile_json"] == {
        "registry_provenance": {"x": 1}
    }


def test_persist_cip_session_commit_called_once(monkeypatch):
    monkeypatch.setattr(
        cb, "merge_registry_provenance_into_profile", MagicMock(return_value={})
    )
    cip = _make_cip()
    session = MagicMock()
    session.get.return_value = _make_company()

    cb.persist_cip(session, cip)

    session.commit.assert_called_once()


def test_persist_cip_missing_row_returns_without_commit(monkeypatch):
    monkeypatch.setattr(
        cb, "merge_registry_provenance_into_profile", MagicMock(return_value={})
    )
    cip = _make_cip()
    session = MagicMock()
    session.get.return_value = None

    result = cb.persist_cip(session, cip)

    assert result is None
    session.commit.assert_not_called()


def test_persist_cip_missing_architecture_row_returns_without_commit(monkeypatch):
    monkeypatch.setattr(
        cb, "merge_registry_provenance_into_profile", MagicMock(return_value={})
    )
    cip = _make_cip(kind="architecture")
    session = MagicMock()
    session.get.return_value = None

    result = cb.persist_cip(session, cip)

    assert result is None
    session.commit.assert_not_called()


# ===================================================================
# get_cip()
# ===================================================================


def test_get_cip_fresh_cached_returned_without_rebuild(monkeypatch):
    build_mock = MagicMock()
    persist_mock = MagicMock()
    monkeypatch.setattr(cb, "build_cip", build_mock)
    monkeypatch.setattr(cb, "persist_cip", persist_mock)

    cip = _make_cip(company_id=1)
    row = _make_company(
        cip_at=datetime.now(timezone.utc),
        cip_json=cip.to_dict(),
    )
    session = MagicMock()
    session.get.return_value = row

    result = cb.get_cip(session, company_id=1, kind="construction")

    build_mock.assert_not_called()
    persist_mock.assert_not_called()
    assert result.company_id == 1
    assert result.primary_trade == cip.primary_trade


def test_get_cip_stale_cache_rebuilds_and_persists(monkeypatch):
    build_mock = MagicMock(return_value=_make_cip(company_id=1))
    persist_mock = MagicMock()
    monkeypatch.setattr(cb, "build_cip", build_mock)
    monkeypatch.setattr(cb, "persist_cip", persist_mock)

    stale_at = datetime.now(timezone.utc) - timedelta(hours=PROFILE_TTL_HOURS + 1)
    row = _make_company(cip_at=stale_at, cip_json={"stale": True})
    session = MagicMock()
    session.get.return_value = row

    result = cb.get_cip(session, company_id=1, kind="construction")

    build_mock.assert_called_once()
    persist_mock.assert_called_once()
    assert result.company_id == 1


def test_get_cip_refresh_true_forces_rebuild_even_if_fresh(monkeypatch):
    build_mock = MagicMock(return_value=_make_cip(company_id=1))
    persist_mock = MagicMock()
    monkeypatch.setattr(cb, "build_cip", build_mock)
    monkeypatch.setattr(cb, "persist_cip", persist_mock)

    fresh_cip = _make_cip(company_id=1)
    row = _make_company(cip_at=datetime.now(timezone.utc), cip_json=fresh_cip.to_dict())
    session = MagicMock()
    session.get.return_value = row

    cb.get_cip(session, company_id=1, kind="construction", refresh=True)

    build_mock.assert_called_once()
    persist_mock.assert_called_once()


def test_get_cip_missing_company_raises_value_error():
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(ValueError, match="Company 999 not found"):
        cb.get_cip(session, company_id=999, kind="construction")


def test_get_cip_selects_company_model_for_construction(monkeypatch):
    from db.models import Company

    build_mock = MagicMock(return_value=_make_cip(company_id=1))
    monkeypatch.setattr(cb, "build_cip", build_mock)
    monkeypatch.setattr(cb, "persist_cip", MagicMock())
    session = MagicMock()
    session.get.return_value = _make_company(cip_at=None, cip_json=None)

    cb.get_cip(session, company_id=1, kind="construction")

    args, _ = session.get.call_args
    assert args[0] is Company


def test_get_cip_selects_arch_company_model_for_architecture(monkeypatch):
    from db.models import ArchCompany

    build_mock = MagicMock(return_value=_make_cip(company_id=10, kind="architecture"))
    monkeypatch.setattr(cb, "build_cip", build_mock)
    monkeypatch.setattr(cb, "persist_cip", MagicMock())
    session = MagicMock()
    session.get.return_value = _make_arch_company(cip_at=None, cip_json=None)

    cb.get_cip(session, company_id=10, kind="architecture")

    args, _ = session.get.call_args
    assert args[0] is ArchCompany


# ===================================================================
# get_capability_profile_from_cip()
# ===================================================================


def test_get_capability_profile_from_cip_forwards_arguments(monkeypatch):
    get_cip_mock = MagicMock(return_value=_make_cip(company_id=5, kind="architecture"))
    monkeypatch.setattr(cb, "get_cip", get_cip_mock)
    session = MagicMock()

    cb.get_capability_profile_from_cip(
        session, company_id=5, kind="architecture", refresh=True
    )

    get_cip_mock.assert_called_once_with(
        session, company_id=5, kind="architecture", refresh=True
    )


def test_get_capability_profile_from_cip_returns_mapped_profile(monkeypatch):
    fixed_cip = _make_cip(company_id=5)
    monkeypatch.setattr(cb, "get_cip", MagicMock(return_value=fixed_cip))
    session = MagicMock()

    result = cb.get_capability_profile_from_cip(
        session, company_id=5, kind="construction"
    )

    assert result.to_dict() == cb.cip_to_capability_profile(fixed_cip).to_dict()
