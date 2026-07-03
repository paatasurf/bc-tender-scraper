"""Operational metrics for Google enrichment service health (not product analytics)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from pipeline.google_enrichment.config import GoogleEnrichmentSettings, load_settings
from pipeline.google_enrichment.constants import (
    enriched,
    error,
    no_match,
    pending,
    review,
    stale,
)

RATE_STATUSES: frozenset[str] = frozenset(
    {"success", "review", "no_match", "error", "rejected"}
)


def compute_rates(counts: dict[str, int]) -> dict[str, float | None]:
    """Derive percentage rates from terminal attempt status counts."""
    denominator = sum(counts.get(status, 0) for status in RATE_STATUSES)
    if denominator == 0:
        return {
            "success_rate_pct": None,
            "manual_review_rate_pct": None,
            "no_match_rate_pct": None,
        }

    def pct(n: int) -> float:
        return round(100.0 * n / denominator, 2)

    return {
        "success_rate_pct": pct(counts.get("success", 0)),
        "manual_review_rate_pct": pct(counts.get("review", 0)),
        "no_match_rate_pct": pct(counts.get("no_match", 0)),
    }


def fetch_operational_metrics(session: Session, settings: GoogleEnrichmentSettings | None = None) -> dict[str, Any]:
    """Load raw metric components from PostgreSQL."""
    cfg = settings or load_settings()

    coverage_row = session.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE google_place_id IS NOT NULL AND google_place_id <> ''
                ) AS with_place_id,
                COUNT(*) AS active_total,
                ROUND(
                    100.0 * COUNT(*) FILTER (
                        WHERE google_place_id IS NOT NULL AND google_place_id <> ''
                    ) / NULLIF(COUNT(*), 0),
                    2
                ) AS coverage_pct
            FROM companies
            WHERE lifecycle_status = 'active' AND is_operating = true
            """
        )
    ).one()

    window_rows = session.execute(
        text(
            """
            SELECT
                status,
                provider,
                COUNT(*) AS cnt,
                AVG(match_confidence) AS avg_confidence,
                AVG(latency_ms) AS avg_latency_ms
            FROM google_enrichment_logs
            WHERE attempted_at >= NOW() - INTERVAL '24 hours'
              AND status <> 'skipped'
            GROUP BY status, provider
            """
        )
    ).all()

    provider_errors = session.execute(
        text(
            """
            SELECT provider, COUNT(*) AS cnt
            FROM google_enrichment_logs
            WHERE attempted_at >= NOW() - INTERVAL '24 hours'
              AND status = 'error'
            GROUP BY provider
            """
        )
    ).all()

    last_run_row = session.execute(
        text(
            """
            SELECT run_id, finished_at, counts_json
            FROM pipeline_runs
            WHERE step = 'google-enrichment'
              AND status = 'success'
            ORDER BY finished_at DESC NULLS LAST
            LIMIT 1
            """
        )
    ).first()

    queue_row = session.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE lifecycle_status = 'active'
                      AND is_operating = true
                      AND google_enrichment_status NOT IN (:review_status)
                      AND (
                            google_place_id IS NULL
                            OR google_enrichment_status IN (:pending_status, :error_status)
                            OR (
                                google_enrichment_status IN (:enriched_status, :stale_status)
                                AND google_last_updated < NOW() - make_interval(days => :stale_days)
                            )
                            OR (
                                google_enrichment_status = :no_match_status
                                AND google_last_updated < NOW() - make_interval(days => :no_match_days)
                            )
                      )
                ) AS eligible,
                COUNT(*) FILTER (
                    WHERE google_enrichment_status = :review_status
                ) AS pending_review,
                COUNT(*) FILTER (
                    WHERE google_enrichment_status = :stale_status
                ) AS stale,
                COUNT(*) FILTER (
                    WHERE google_enrichment_status = :no_match_status
                ) AS no_match
            FROM companies
            """
        ),
        {
            "stale_days": cfg.stale_days,
            "no_match_days": cfg.no_match_retry_days,
            "review_status": review,
            "pending_status": pending,
            "error_status": error,
            "enriched_status": enriched,
            "stale_status": stale,
            "no_match_status": no_match,
        },
    ).one()

    return {
        "coverage": {
            "active_companies": int(coverage_row.active_total or 0),
            "with_place_id": int(coverage_row.with_place_id or 0),
            "coverage_pct": float(coverage_row.coverage_pct or 0.0),
        },
        "window_rows": window_rows,
        "provider_errors": provider_errors,
        "last_run_row": last_run_row,
        "queue": {
            "eligible": int(queue_row.eligible or 0),
            "pending_review": int(queue_row.pending_review or 0),
            "stale": int(queue_row.stale or 0),
            "no_match": int(queue_row.no_match or 0),
        },
    }


def build_metrics_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Shape operational metrics into the stable internal API contract."""
    status_counts: dict[str, int] = {}
    confidence_weighted = 0.0
    confidence_count = 0
    latency_weighted = 0.0
    latency_count = 0

    for row in raw.get("window_rows", []):
        status = str(row.status)
        cnt = int(row.cnt or 0)
        status_counts[status] = status_counts.get(status, 0) + cnt
        if status == "success" and row.avg_confidence is not None:
            confidence_weighted += float(row.avg_confidence) * cnt
            confidence_count += cnt
        if row.avg_latency_ms is not None:
            latency_weighted += float(row.avg_latency_ms) * cnt
            latency_count += cnt

    attempts = sum(status_counts.get(s, 0) for s in RATE_STATUSES)
    rates = compute_rates(status_counts)

    provider_error_map: dict[str, int] = {}
    for row in raw.get("provider_errors", []):
        provider_error_map[str(row.provider)] = int(row.cnt or 0)

    last_run_payload: dict[str, Any] | None = None
    last_run_row = raw.get("last_run_row")
    if last_run_row is not None:
        counts: dict[str, Any] = {}
        try:
            counts = json.loads(last_run_row.counts_json or "{}")
        except json.JSONDecodeError:
            counts = {}
        last_run_payload = {
            "run_id": last_run_row.run_id,
            "finished_at": (
                last_run_row.finished_at.isoformat() if last_run_row.finished_at else None
            ),
            "success_rate_pct": counts.get("success_rate"),
            "attempts": counts.get("attempted"),
        }

    avg_confidence = (
        round(confidence_weighted / confidence_count, 4) if confidence_count else None
    )
    avg_latency = round(latency_weighted / latency_count) if latency_count else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": raw["coverage"],
        "window_24h": {
            "attempts": attempts,
            "success": status_counts.get("success", 0),
            "review": status_counts.get("review", 0),
            "no_match": status_counts.get("no_match", 0),
            "error": status_counts.get("error", 0),
            "rejected": status_counts.get("rejected", 0),
            "success_rate_pct": rates["success_rate_pct"],
            "manual_review_rate_pct": rates["manual_review_rate_pct"],
            "no_match_rate_pct": rates["no_match_rate_pct"],
            "avg_confidence": avg_confidence,
            "avg_lookup_latency_ms": avg_latency,
            "provider_errors": {
                **provider_error_map,
                "total": sum(provider_error_map.values()),
            },
        },
        "last_run": last_run_payload,
        "queue": raw["queue"],
    }


def get_google_enrichment_metrics(session: Session) -> dict[str, Any]:
    """Fetch and format operational metrics for the internal API."""
    raw = fetch_operational_metrics(session)
    return build_metrics_payload(raw)
