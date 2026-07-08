"""Company Intelligence Profile (CIP) v2 schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["construction", "architecture"]
CIP_VERSION = 2


@dataclass
class ValueRange:
    p25: float = 0.0
    median: float = 0.0
    p75: float = 0.0
    max: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class GeoConcentration:
    geo: str
    share: float
    project_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectCluster:
    delivery: str
    sector: str
    geo: str
    count: int
    share: float
    avg_value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompanyIntelligenceProfile:
    version: int
    computed_at: str
    company_id: int
    kind: Kind
    name: str
    company_type: str
    entity_class: str
    primary_trade: str
    secondary_trades: list[str]
    trade_sources: list[str]
    specialization_confidence: float
    delivery_types: list[str]
    normalized_project_types: list[str]
    sector_focus: dict[str, float]
    dominant_sector: str
    sector_confidence: str
    work_orientation: str
    buyer_types: list[str]
    client_types: list[str]
    public_private_ratio: float
    procurement_affinity: str
    service_cities: list[str]
    neighborhoods: list[str]
    concentration_map: list[GeoConcentration]
    geographic_reach: str
    value_range: ValueRange
    typical_project_value: float
    deal_size_band: str
    project_clusters: list[ProjectCluster]
    own_permit_count: int
    award_count: int
    award_categories: list[str]
    award_clients: list[str]
    architect_partners: list[dict[str, Any]]
    repeat_clients: list[str]
    growth_direction: list[str]
    expansion_confidence: float
    profile_completeness: float
    normalized_name: str
    market_segments: list[str] = field(default_factory=list)
    specializations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "computed_at": self.computed_at,
            "company_id": self.company_id,
            "kind": self.kind,
            "name": self.name,
            "company_type": self.company_type,
            "entity_class": self.entity_class,
            "primary_trade": self.primary_trade,
            "secondary_trades": self.secondary_trades,
            "trade_sources": self.trade_sources,
            "specialization_confidence": self.specialization_confidence,
            "delivery_types": self.delivery_types,
            "normalized_project_types": self.normalized_project_types,
            "sector_focus": self.sector_focus,
            "dominant_sector": self.dominant_sector,
            "sector_confidence": self.sector_confidence,
            "work_orientation": self.work_orientation,
            "buyer_types": self.buyer_types,
            "client_types": self.client_types,
            "public_private_ratio": self.public_private_ratio,
            "procurement_affinity": self.procurement_affinity,
            "service_cities": self.service_cities,
            "neighborhoods": self.neighborhoods,
            "concentration_map": [g.to_dict() for g in self.concentration_map],
            "geographic_reach": self.geographic_reach,
            "value_range": self.value_range.to_dict(),
            "typical_project_value": self.typical_project_value,
            "deal_size_band": self.deal_size_band,
            "project_clusters": [c.to_dict() for c in self.project_clusters],
            "own_permit_count": self.own_permit_count,
            "award_count": self.award_count,
            "award_categories": self.award_categories,
            "award_clients": self.award_clients,
            "architect_partners": self.architect_partners,
            "repeat_clients": self.repeat_clients,
            "growth_direction": self.growth_direction,
            "expansion_confidence": self.expansion_confidence,
            "profile_completeness": self.profile_completeness,
            "normalized_name": self.normalized_name,
            "market_segments": self.market_segments,
            "specializations": self.specializations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompanyIntelligenceProfile:
        vr = data.get("value_range") or {}
        return cls(
            version=int(data.get("version", CIP_VERSION)),
            computed_at=str(data.get("computed_at", "")),
            company_id=int(data["company_id"]),
            kind=data.get("kind", "construction"),
            name=str(data.get("name", "")),
            company_type=str(data.get("company_type", "")),
            entity_class=str(data.get("entity_class", "contractor")),
            primary_trade=str(data.get("primary_trade", "")),
            secondary_trades=list(data.get("secondary_trades") or []),
            trade_sources=list(data.get("trade_sources") or []),
            specialization_confidence=float(data.get("specialization_confidence", 0)),
            delivery_types=list(data.get("delivery_types") or []),
            normalized_project_types=list(data.get("normalized_project_types") or []),
            sector_focus=dict(data.get("sector_focus") or {}),
            dominant_sector=str(data.get("dominant_sector", "")),
            sector_confidence=str(data.get("sector_confidence", "")),
            work_orientation=str(data.get("work_orientation", "construction")),
            buyer_types=list(data.get("buyer_types") or []),
            client_types=list(data.get("client_types") or []),
            public_private_ratio=float(data.get("public_private_ratio", 0)),
            procurement_affinity=str(data.get("procurement_affinity", "project")),
            service_cities=list(data.get("service_cities") or []),
            neighborhoods=list(data.get("neighborhoods") or []),
            concentration_map=[
                GeoConcentration(**g) for g in (data.get("concentration_map") or [])
            ],
            geographic_reach=str(data.get("geographic_reach", "local")),
            value_range=ValueRange(
                p25=float(vr.get("p25", 0)),
                median=float(vr.get("median", 0)),
                p75=float(vr.get("p75", 0)),
                max=float(vr.get("max", 0)),
            ),
            typical_project_value=float(data.get("typical_project_value", 0)),
            deal_size_band=str(data.get("deal_size_band", "")),
            project_clusters=[
                ProjectCluster(**c) for c in (data.get("project_clusters") or [])
            ],
            own_permit_count=int(data.get("own_permit_count", 0)),
            award_count=int(data.get("award_count", 0)),
            award_categories=list(data.get("award_categories") or []),
            award_clients=list(data.get("award_clients") or []),
            architect_partners=list(data.get("architect_partners") or []),
            repeat_clients=list(data.get("repeat_clients") or []),
            growth_direction=list(data.get("growth_direction") or []),
            expansion_confidence=float(data.get("expansion_confidence", 0)),
            profile_completeness=float(data.get("profile_completeness", 0)),
            normalized_name=str(data.get("normalized_name", "")),
            market_segments=list(data.get("market_segments") or []),
            specializations=list(data.get("specializations") or []),
        )
