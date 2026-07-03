"""Persist Google enrichment logs and review queue rows."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.models import GoogleEnrichmentLog, GoogleEnrichmentReview
from pipeline.google_enrichment.constants import pending
from pipeline.google_enrichment.models import GoogleEnrichmentLogRecord, LookupEvaluation
from pipeline.google_enrichment.state_machine import InvalidStateTransitionError, can_transition, transition


def persist_log_record(session: Session, record: GoogleEnrichmentLogRecord) -> GoogleEnrichmentLog:
    row = GoogleEnrichmentLog(
        company_id=record.company_id,
        run_id=record.run_id,
        attempted_at=record.attempted_at or datetime.now(timezone.utc),
        query_used=(record.query_used or "")[:500],
        provider=record.provider,
        status=record.status,
        match_confidence=record.match_confidence,
        google_place_id=record.google_place_id,
        candidate_count=record.candidate_count,
        candidate_snapshot=record.candidate_snapshot,
        error_message=record.error_message or "",
        latency_ms=record.latency_ms,
        external_run_id=record.external_run_id or "",
    )
    session.add(row)
    return row


def persist_review_row(
    session: Session,
    *,
    company_id: int,
    run_id: str,
    query_used: str,
    evaluation: LookupEvaluation,
) -> GoogleEnrichmentReview:
    row = GoogleEnrichmentReview(
        company_id=company_id,
        run_id=run_id,
        query_used=(query_used or "")[:500],
        match_confidence=float(evaluation.log_record.match_confidence or 0.0),
        candidate_snapshot=evaluation.log_record.candidate_snapshot or [],
        status="pending",
    )
    session.add(row)
    return row


def set_enrichment_status(company, target_status: str) -> None:
    """Apply a validated google_enrichment_status transition."""
    current = company.google_enrichment_status or pending
    if current == target_status:
        return
    if can_transition(current, target_status):
        company.google_enrichment_status = transition(current, target_status)
        return
    if current == "error" and target_status != pending:
        company.google_enrichment_status = transition(current, pending)
        set_enrichment_status(company, target_status)
        return
    raise InvalidStateTransitionError(current, target_status)


def apply_status_only_outcome(
    session: Session,
    company,
    evaluation: LookupEvaluation,
    *,
    query_used: str,
) -> None:
    """Update status/metadata without writing Google profile fields."""
    now = datetime.now(timezone.utc)
    set_enrichment_status(company, evaluation.enrichment_status)
    company.google_query_used = (query_used or "")[:500]
    if evaluation.log_record.match_confidence is not None:
        company.google_match_confidence = evaluation.log_record.match_confidence
    if evaluation.enrichment_status in {"no_match", "error"}:
        company.google_last_updated = now
