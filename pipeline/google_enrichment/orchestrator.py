"""Batch enrichment orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from db.models import Company
from pipeline.google_enrichment.config import GoogleEnrichmentSettings, load_settings
from pipeline.google_enrichment.constants import enriched
from pipeline.google_enrichment.eligibility import (
    fetch_eligible_companies,
    fetch_reserved_place_ids,
    mark_stale_companies,
)
from pipeline.google_enrichment.log_contract import (
    LOG_STATUS_ERROR,
    LOG_STATUS_REVIEW,
    LOG_STATUS_SUCCESS,
    evaluate_lookup,
)
from pipeline.google_enrichment.metrics import compute_rates
from pipeline.google_enrichment.models import GoogleEnrichmentResult, LookupEvaluation
from pipeline.google_enrichment.persistence import (
    apply_status_only_outcome,
    persist_log_record,
    persist_review_row,
    set_enrichment_status,
)
from pipeline.google_enrichment.provider import GoogleEnrichmentProvider, get_fallback_provider, get_provider
from pipeline.google_enrichment.query_builder import build_refresh_query, company_match_context
from pipeline.google_enrichment.writer import CompanyGoogleWriter

logger = logging.getLogger(__name__)

STEP_NAME = "google-enrichment"


@dataclass
class CompanyRunResult:
    company_id: int
    company_name: str
    query_used: str
    log_status: str
    enrichment_status: str
    match_confidence: float | None = None
    google_place_id: str | None = None
    candidate_count: int = 0
    latency_ms: int = 0
    error_message: str = ""
    dry_run: bool = False
    candidate_snapshot: list[dict[str, Any]] | None = None


@dataclass
class OrchestratorRunResult:
    run_id: str
    dry_run: bool
    provider: str
    provider_fallback_used: bool = False
    marked_stale: int = 0
    attempted: int = 0
    success: int = 0
    review: int = 0
    no_match: int = 0
    error: int = 0
    rejected: int = 0
    skipped: int = 0
    avg_confidence: float | None = None
    avg_latency_ms: int | None = None
    companies: list[CompanyRunResult] = field(default_factory=list)

    def counts(self) -> dict[str, Any]:
        rates = compute_rates(
            {
                "success": self.success,
                "review": self.review,
                "no_match": self.no_match,
                "error": self.error,
                "rejected": self.rejected,
            }
        )
        return {
            "attempted": self.attempted,
            "success": self.success,
            "review": self.review,
            "no_match": self.no_match,
            "error": self.error,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "marked_stale": self.marked_stale,
            "dry_run": self.dry_run,
            "provider": self.provider,
            "provider_fallback_used": self.provider_fallback_used,
            "avg_confidence": self.avg_confidence,
            "avg_latency_ms": self.avg_latency_ms,
            **rates,
        }


class GoogleEnrichmentOrchestrator:
    """Coordinates provider lookup, matching, writing, and logging."""

    def __init__(
        self,
        *,
        settings: GoogleEnrichmentSettings | None = None,
        provider: GoogleEnrichmentProvider | None = None,
        fallback_provider: GoogleEnrichmentProvider | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._provider = provider
        self._fallback_provider = fallback_provider

    def run(
        self,
        session: Session,
        *,
        run_id: str | None = None,
        dry_run: bool = False,
        batch_size: int | None = None,
        company_ids: list[int] | None = None,
    ) -> OrchestratorRunResult:
        actual_run_id = run_id or str(uuid.uuid4())
        provider, fallback_used = self._resolve_provider()
        result = OrchestratorRunResult(
            run_id=actual_run_id,
            dry_run=dry_run,
            provider=provider.provider_name,
            provider_fallback_used=fallback_used,
        )

        if not dry_run:
            result.marked_stale = mark_stale_companies(session, self.settings)

        companies = fetch_eligible_companies(
            session,
            self.settings,
            batch_size=batch_size,
            company_ids=company_ids,
        )
        if not companies:
            return result

        confidences: list[float] = []
        latencies: list[int] = []

        for company in companies:
            company_result = self._process_company(
                session,
                company,
                provider=provider,
                run_id=actual_run_id,
                dry_run=dry_run,
            )
            result.companies.append(company_result)
            result.attempted += 1
            status = company_result.log_status
            if status == LOG_STATUS_SUCCESS:
                result.success += 1
            elif status == LOG_STATUS_REVIEW:
                result.review += 1
            elif status == LOG_STATUS_ERROR:
                result.error += 1
            elif status == "no_match":
                result.no_match += 1
            elif status == "rejected":
                result.rejected += 1

            if company_result.match_confidence is not None:
                confidences.append(company_result.match_confidence)
            if company_result.latency_ms:
                latencies.append(company_result.latency_ms)

        if confidences:
            result.avg_confidence = round(sum(confidences) / len(confidences), 4)
        if latencies:
            result.avg_latency_ms = round(sum(latencies) / len(latencies))

        if not dry_run:
            session.commit()

        return result

    def _resolve_provider(self) -> tuple[GoogleEnrichmentProvider, bool]:
        primary = self._provider or get_provider(self.settings)
        fallback = self._fallback_provider or get_fallback_provider(self.settings)
        if asyncio.run(primary.healthcheck()):
            return primary, False
        if asyncio.run(fallback.healthcheck()):
            logger.warning(
                "Primary Google provider %s unavailable; using fallback %s",
                primary.provider_name,
                fallback.provider_name,
            )
            return fallback, True
        raise RuntimeError(
            f"Google enrichment providers unavailable: primary={primary.provider_name}, "
            f"fallback={fallback.provider_name}"
        )

    def _process_company(
        self,
        session: Session,
        company: Company,
        *,
        provider: GoogleEnrichmentProvider,
        run_id: str,
        dry_run: bool,
    ) -> CompanyRunResult:
        query_used = build_refresh_query(company)
        context = company_match_context(company)
        reserved = fetch_reserved_place_ids(session, exclude_company_id=company.id)

        started = time.perf_counter()
        provider_error: str | None = None
        external_run_id = ""
        candidates = []
        try:
            candidates = asyncio.run(provider.lookup(query_used, limit=3))
        except Exception as exc:
            provider_error = str(exc)
            logger.exception(
                "Google lookup failed company_id=%s query=%s",
                company.id,
                query_used,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)

        evaluation = evaluate_lookup(
            context,
            candidates=candidates,
            provider=provider.provider_name,
            query_used=query_used,
            run_id=run_id,
            latency_ms=latency_ms,
            settings=self.settings,
            reserved_place_ids=reserved,
            provider_error=provider_error,
            external_run_id=external_run_id,
        )

        if not dry_run:
            self._persist_outcome(
                session,
                company,
                evaluation=evaluation,
                query_used=query_used,
                run_id=run_id,
            )

        return CompanyRunResult(
            company_id=company.id,
            company_name=company.name,
            query_used=query_used,
            log_status=evaluation.log_record.status,
            enrichment_status=evaluation.enrichment_status,
            match_confidence=evaluation.log_record.match_confidence,
            google_place_id=evaluation.log_record.google_place_id,
            candidate_count=evaluation.log_record.candidate_count,
            latency_ms=latency_ms,
            error_message=evaluation.log_record.error_message,
            dry_run=dry_run,
            candidate_snapshot=evaluation.log_record.candidate_snapshot,
        )

    def _persist_outcome(
        self,
        session: Session,
        company: Company,
        *,
        evaluation: LookupEvaluation,
        query_used: str,
        run_id: str,
    ) -> None:
        persist_log_record(session, evaluation.log_record)
        log_status = evaluation.log_record.status

        if log_status == LOG_STATUS_SUCCESS and evaluation.best is not None:
            now = datetime.now(timezone.utc)
            result = GoogleEnrichmentResult(
                company_id=company.id,
                place=evaluation.best.candidate,
                match_confidence=float(evaluation.log_record.match_confidence or 0.0),
                google_enrichment_status=enriched,
                query_used=query_used,
                match_breakdown=evaluation.best.breakdown,
                google_last_updated=now,
                google_last_seen=now,
            )
            CompanyGoogleWriter().apply(session, company.id, result)
            set_enrichment_status(company, enriched)
            if self.settings.copy_website_to_website:
                website = (company.website or "").strip()
                google_website = (evaluation.best.candidate.google_website or "").strip()
                if not website and google_website:
                    company.website = google_website[:500]
            return

        if log_status == LOG_STATUS_REVIEW:
            persist_review_row(
                session,
                company_id=company.id,
                run_id=run_id,
                query_used=query_used,
                evaluation=evaluation,
            )
            apply_status_only_outcome(session, company, evaluation, query_used=query_used)
            return

        apply_status_only_outcome(session, company, evaluation, query_used=query_used)


def run_google_enrichment(
    session: Session,
    *,
    run_id: str | None = None,
    dry_run: bool = False,
    batch_size: int | None = None,
    company_ids: list[int] | None = None,
    settings: GoogleEnrichmentSettings | None = None,
) -> dict[str, Any]:
    orchestrator = GoogleEnrichmentOrchestrator(settings=settings)
    result = orchestrator.run(
        session,
        run_id=run_id,
        dry_run=dry_run,
        batch_size=batch_size,
        company_ids=company_ids,
    )
    payload = result.counts()
    payload["run_id"] = result.run_id
    payload["companies"] = [
        {
            "company_id": item.company_id,
            "company_name": item.company_name,
            "query_used": item.query_used,
            "log_status": item.log_status,
            "enrichment_status": item.enrichment_status,
            "match_confidence": item.match_confidence,
            "google_place_id": item.google_place_id,
            "candidate_count": item.candidate_count,
            "latency_ms": item.latency_ms,
            "error_message": item.error_message,
            "candidate_snapshot": item.candidate_snapshot,
        }
        for item in result.companies
    ]
    return payload
