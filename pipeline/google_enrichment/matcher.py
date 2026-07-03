"""Deterministic place matching (Phase 1)."""

from __future__ import annotations

from pipeline.google_enrichment.models import (
    CompanyMatchContext,
    MatchBreakdown,
    PlaceCandidate,
    ScoredCandidate,
)
from pipeline.google_enrichment.normalize import (
    address_similarity,
    city_match_score,
    is_province_outside_bc,
    name_similarity,
    phone_match_score,
    province_match_score,
)

WEIGHT_NAME = 0.40
WEIGHT_CITY = 0.25
WEIGHT_PROVINCE = 0.10
WEIGHT_ADDRESS = 0.15
WEIGHT_PHONE = 0.10

HARD_REJECT_DUPLICATE_PLACE_ID = "duplicate_place_id"
HARD_REJECT_PROVINCE_OUTSIDE_BC = "province_outside_bc"


class PlaceMatcher:
    """Scores provider candidates against company records — deterministic, no AI."""

    def score(
        self,
        company_name: str,
        company_city: str,
        company_province: str,
        company_address: str,
        company_phone: str,
        candidate: PlaceCandidate,
        *,
        reserved_place_ids: frozenset[str] | None = None,
    ) -> MatchBreakdown:
        return self.score_context(
            CompanyMatchContext(
                company_id=0,
                name=company_name,
                city=company_city,
                province=company_province,
                address=company_address,
                phone=company_phone,
            ),
            candidate,
            reserved_place_ids=reserved_place_ids,
        ).breakdown

    def score_context(
        self,
        context: CompanyMatchContext,
        candidate: PlaceCandidate,
        *,
        reserved_place_ids: frozenset[str] | None = None,
    ) -> ScoredCandidate:
        reject_reason = self._hard_reject_reason(candidate, reserved_place_ids)
        if reject_reason:
            return ScoredCandidate(
                candidate=candidate,
                breakdown=MatchBreakdown(),
                hard_rejected=True,
                reject_reason=reject_reason,
            )

        breakdown = MatchBreakdown(
            name_score=round(name_similarity(context.name, candidate.name), 4),
            city_score=round(
                city_match_score(context.city, candidate.formatted_address), 4
            ),
            province_score=round(
                province_match_score(context.province, candidate.formatted_address),
                4,
            ),
            address_score=round(
                address_similarity(context.address, candidate.formatted_address), 4
            ),
            phone_score=round(phone_match_score(context.phone, candidate.phone), 4),
        )
        return ScoredCandidate(candidate=candidate, breakdown=breakdown)

    def rank_candidates(
        self,
        context: CompanyMatchContext,
        candidates: list[PlaceCandidate],
        *,
        reserved_place_ids: frozenset[str] | None = None,
    ) -> list[ScoredCandidate]:
        scored = [
            self.score_context(context, candidate, reserved_place_ids=reserved_place_ids)
            for candidate in candidates
        ]
        return sorted(
            scored,
            key=lambda item: (-item.confidence, item.candidate.place_id),
        )

    @staticmethod
    def _hard_reject_reason(
        candidate: PlaceCandidate,
        reserved_place_ids: frozenset[str] | None,
    ) -> str:
        if reserved_place_ids and candidate.place_id in reserved_place_ids:
            return HARD_REJECT_DUPLICATE_PLACE_ID
        if is_province_outside_bc(candidate.formatted_address):
            return HARD_REJECT_PROVINCE_OUTSIDE_BC
        return ""
