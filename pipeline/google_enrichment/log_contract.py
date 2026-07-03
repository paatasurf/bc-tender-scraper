"""In-memory log contract for Google enrichment lookup attempts (Phase 1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pipeline.google_enrichment.config import GoogleEnrichmentSettings, load_settings
from pipeline.google_enrichment.constants import enriched, error, no_match, review
from pipeline.google_enrichment.matcher import PlaceMatcher
from pipeline.google_enrichment.models import (
    CompanyMatchContext,
    GoogleEnrichmentLogRecord,
    LookupEvaluation,
    PlaceCandidate,
    ScoredCandidate,
)

LOG_STATUS_SUCCESS = "success"
LOG_STATUS_REVIEW = "review"
LOG_STATUS_NO_MATCH = "no_match"
LOG_STATUS_ERROR = "error"
LOG_STATUS_REJECTED = "rejected"
LOG_STATUS_SKIPPED = "skipped"


def build_candidate_snapshot(
    scored: list[ScoredCandidate],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for item in scored[:limit]:
        snapshot.append(
            {
                "place_id": item.candidate.place_id,
                "name": item.candidate.name,
                "formatted_address": item.candidate.formatted_address,
                "confidence": item.confidence,
                "hard_rejected": item.hard_rejected,
                "reject_reason": item.reject_reason,
                "breakdown": {
                    "name_score": item.breakdown.name_score,
                    "city_score": item.breakdown.city_score,
                    "province_score": item.breakdown.province_score,
                    "address_score": item.breakdown.address_score,
                    "phone_score": item.breakdown.phone_score,
                    "total_score": item.breakdown.total_score,
                },
            }
        )
    return snapshot


def _resolve_outcomes(
    *,
    candidate_count: int,
    best: ScoredCandidate | None,
    settings: GoogleEnrichmentSettings,
    provider_error: str | None,
) -> tuple[str, str, float | None]:
    """Return (log_status, enrichment_status, match_confidence)."""
    if provider_error:
        return LOG_STATUS_ERROR, error, None

    if candidate_count == 0 or best is None:
        return LOG_STATUS_NO_MATCH, no_match, None

    confidence = best.confidence
    if best.hard_rejected or confidence < settings.confidence_review:
        return LOG_STATUS_REJECTED, no_match, confidence

    if confidence >= settings.confidence_accept:
        return LOG_STATUS_SUCCESS, enriched, confidence

    return LOG_STATUS_REVIEW, review, confidence


def build_log_record(
    *,
    company_id: int,
    run_id: str,
    provider: str,
    query_used: str,
    status: str,
    latency_ms: int,
    candidate_count: int,
    match_confidence: float | None = None,
    candidate_snapshot: list[dict[str, Any]] | None = None,
    google_place_id: str | None = None,
    error_message: str = "",
    external_run_id: str = "",
    attempted_at: datetime | None = None,
) -> GoogleEnrichmentLogRecord:
    return GoogleEnrichmentLogRecord(
        company_id=company_id,
        run_id=run_id,
        provider=provider,
        query_used=query_used,
        status=status,
        latency_ms=latency_ms,
        candidate_count=candidate_count,
        match_confidence=match_confidence,
        candidate_snapshot=candidate_snapshot,
        google_place_id=google_place_id,
        error_message=error_message,
        external_run_id=external_run_id,
        attempted_at=attempted_at or datetime.now(timezone.utc),
    )


def evaluate_lookup(
    context: CompanyMatchContext,
    *,
    candidates: list[PlaceCandidate],
    provider: str,
    query_used: str,
    run_id: str,
    latency_ms: int,
    settings: GoogleEnrichmentSettings | None = None,
    reserved_place_ids: frozenset[str] | None = None,
    provider_error: str | None = None,
    external_run_id: str = "",
    attempted_at: datetime | None = None,
) -> LookupEvaluation:
    """Score fixture candidates and build the mandatory log record — no DB writes."""
    cfg = settings or load_settings()
    matcher = PlaceMatcher()

    scored: list[ScoredCandidate] = []
    if provider_error is None:
        scored = matcher.rank_candidates(
            context,
            candidates,
            reserved_place_ids=reserved_place_ids,
        )

    best = scored[0] if scored else None
    candidate_count = len(candidates)
    log_status, enrichment_status, match_confidence = _resolve_outcomes(
        candidate_count=candidate_count,
        best=best,
        settings=cfg,
        provider_error=provider_error,
    )

    log_record = build_log_record(
        company_id=context.company_id,
        run_id=run_id,
        provider=provider,
        query_used=query_used,
        status=log_status,
        latency_ms=latency_ms,
        candidate_count=candidate_count,
        match_confidence=match_confidence,
        candidate_snapshot=build_candidate_snapshot(scored),
        google_place_id=best.candidate.place_id if best and log_status == LOG_STATUS_SUCCESS else None,
        error_message=provider_error or "",
        external_run_id=external_run_id,
        attempted_at=attempted_at,
    )
    return LookupEvaluation(
        log_record=log_record,
        enrichment_status=enrichment_status,
        best=best,
    )
