"""Unit tests for Google enrichment operational metrics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pipeline.google_enrichment.metrics import (
    RATE_STATUSES,
    build_metrics_payload,
    compute_rates,
    fetch_operational_metrics,
)


def test_compute_rates_empty_denominator():
    rates = compute_rates({})
    assert rates["success_rate_pct"] is None
    assert rates["manual_review_rate_pct"] is None
    assert rates["no_match_rate_pct"] is None


def test_compute_rates_from_status_counts():
    counts = {
        "success": 14,
        "review": 2,
        "no_match": 4,
        "error": 1,
        "rejected": 0,
    }
    rates = compute_rates(counts)
    assert rates["success_rate_pct"] == pytest.approx(66.67, abs=0.01)
    assert rates["manual_review_rate_pct"] == pytest.approx(9.52, abs=0.01)
    assert rates["no_match_rate_pct"] == pytest.approx(19.05, abs=0.01)


def test_rate_statuses_exclude_skipped():
    assert "skipped" not in RATE_STATUSES
    assert "success" in RATE_STATUSES
    assert "review" in RATE_STATUSES


def test_build_metrics_payload_shape():
    raw = {
        "coverage": {
            "active_companies": 616,
            "with_place_id": 77,
            "coverage_pct": 12.5,
        },
        "window_rows": [
            SimpleNamespace(
                status="success",
                provider="apify",
                cnt=14,
                avg_confidence=0.834,
                avg_latency_ms=4120,
            ),
            SimpleNamespace(
                status="review",
                provider="apify",
                cnt=2,
                avg_confidence=0.62,
                avg_latency_ms=3900,
            ),
            SimpleNamespace(
                status="no_match",
                provider="apify",
                cnt=4,
                avg_confidence=None,
                avg_latency_ms=3500,
            ),
            SimpleNamespace(
                status="error",
                provider="apify",
                cnt=1,
                avg_confidence=None,
                avg_latency_ms=8000,
            ),
        ],
        "provider_errors": [SimpleNamespace(provider="apify", cnt=1)],
        "last_run_row": SimpleNamespace(
            run_id="run-123",
            finished_at=None,
            counts_json='{"attempted": 21, "success_rate": 66.7}',
        ),
        "queue": {
            "eligible": 580,
            "pending_review": 5,
            "stale": 120,
            "no_match": 45,
        },
    }
    payload = build_metrics_payload(raw)
    assert payload["coverage"]["coverage_pct"] == 12.5
    assert payload["window_24h"]["attempts"] == 21
    assert payload["window_24h"]["success"] == 14
    assert payload["window_24h"]["provider_errors"]["total"] == 1
    assert payload["window_24h"]["avg_confidence"] == pytest.approx(0.834, abs=0.001)
    assert payload["queue"]["eligible"] == 580
    assert "generated_at" in payload


def test_fetch_operational_metrics_executes_sql():
    session = MagicMock()

    def fake_execute(statement, params=None):
        sql = str(statement)
        if "coverage_pct" in sql:
            return SimpleNamespace(
                one=lambda: SimpleNamespace(with_place_id=10, active_total=100, coverage_pct=10.0)
            )
        if "google_enrichment_logs" in sql and "GROUP BY status" in sql:
            return SimpleNamespace(all=lambda: [])
        if "status = 'error'" in sql:
            return SimpleNamespace(all=lambda: [])
        if "pipeline_runs" in sql:
            return SimpleNamespace(first=lambda: None)
        if "eligible" in sql:
            return SimpleNamespace(
                one=lambda: SimpleNamespace(eligible=5, pending_review=1, stale=2, no_match=3)
            )
        return SimpleNamespace(all=lambda: [], one=lambda: None, first=lambda: None)

    session.execute.side_effect = fake_execute
    raw = fetch_operational_metrics(session)
    assert raw["coverage"]["coverage_pct"] == 10.0
    assert raw["queue"]["eligible"] == 5
    assert session.execute.call_count >= 4
