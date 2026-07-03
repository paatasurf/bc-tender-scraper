"""Domain models for Google enrichment pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PlaceCandidate:
    place_id: str
    name: str
    rating: float | None = None
    review_count: int | None = None
    category: str = ""
    formatted_address: str = ""
    phone: str = ""
    google_maps_url: str = ""
    google_website: str = ""
    business_status: str = ""
    lat: float | None = None
    lng: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchBreakdown:
    name_score: float = 0.0
    city_score: float = 0.0
    province_score: float = 0.0
    address_score: float = 0.0
    phone_score: float = 0.0

    @property
    def total_score(self) -> float:
        raw = (
            0.40 * self.name_score
            + 0.25 * self.city_score
            + 0.10 * self.province_score
            + 0.15 * self.address_score
            + 0.10 * self.phone_score
        )
        return min(1.0, round(raw, 4))


@dataclass(frozen=True)
class CompanyMatchContext:
    company_id: int
    name: str
    city: str = ""
    province: str = "BC"
    address: str = ""
    phone: str = ""


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: PlaceCandidate
    breakdown: MatchBreakdown
    hard_rejected: bool = False
    reject_reason: str = ""

    @property
    def confidence(self) -> float:
        if self.hard_rejected:
            return 0.0
        return self.breakdown.total_score


@dataclass(frozen=True)
class GoogleEnrichmentResult:
    company_id: int
    place: PlaceCandidate
    match_confidence: float
    google_enrichment_status: str
    query_used: str
    match_breakdown: MatchBreakdown | None = None
    google_last_updated: datetime | None = None
    google_last_seen: datetime | None = None


@dataclass(frozen=True)
class GoogleEnrichmentLogRecord:
    company_id: int
    run_id: str
    provider: str
    query_used: str
    status: str
    latency_ms: int
    candidate_count: int
    match_confidence: float | None = None
    candidate_snapshot: list[dict[str, Any]] | None = None
    google_place_id: str | None = None
    error_message: str = ""
    external_run_id: str = ""
    attempted_at: datetime | None = None


@dataclass(frozen=True)
class LookupEvaluation:
    log_record: GoogleEnrichmentLogRecord
    enrichment_status: str
    best: ScoredCandidate | None = None
