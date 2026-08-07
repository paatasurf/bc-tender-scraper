"""Regression tests for pipeline/error_classification.py -- extracted in
M3B from pipeline/ops_read_model.py so pipeline/job_run.py can share the
exact same "never return raw error text" logic instead of duplicating it.
These tests are the same security-critical assertions
tests/unit/test_ops_read_model.py already had for classify_run_error()
before the extraction -- kept here as the canonical location, and
tests/unit/test_ops_read_model.py continues to pass unchanged via
`from pipeline.error_classification import classify_run_error`.
"""

from __future__ import annotations

import pytest

from pipeline.error_classification import ERROR_SUMMARY_LABELS, classify_run_error


def test_error_summary_labels_is_the_documented_fixed_set():
    assert ERROR_SUMMARY_LABELS == {
        "timeout",
        "http_4xx",
        "http_5xx",
        "database",
        "validation",
        "unknown",
    }


def test_classify_run_error_none_or_empty_is_not_present():
    assert classify_run_error(None) == (False, None)
    assert classify_run_error("") == (False, None)


def test_classify_run_error_returns_only_fixed_labels():
    for raw in (
        "connection timed out after 30s",
        "HTTP 503 Service Unavailable",
        "HTTP 404 Not Found",
        "psycopg2.OperationalError: could not connect to server",
        "ValueError: invalid literal for int()",
        "completely unrecognized failure mode",
    ):
        present, summary = classify_run_error(raw)
        assert present is True
        assert summary in ERROR_SUMMARY_LABELS


@pytest.mark.parametrize(
    "raw_secret",
    [
        "postgresql://user:password@host/db",
        "Authorization: Bearer secret-value",
        "api_key=secret-value",
        "Failed to connect: postgresql://scraper_user:hunter2@10.0.0.5:5432/production",
        "External call failed -- Authorization: Bearer sk_live_abcdef123456",
    ],
)
def test_classify_run_error_never_leaks_secret_fragments(raw_secret):
    present, summary = classify_run_error(raw_secret)
    assert present is True
    assert summary in ERROR_SUMMARY_LABELS
    for leaked_marker in (
        "password",
        "hunter2",
        "secret-value",
        "sk_live",
        "Bearer",
        "user:",
    ):
        assert leaked_marker not in summary


def test_classify_run_error_priority_order_timeout_before_http_before_database():
    # A string that could match multiple categories -- timeout must win,
    # matching the documented priority order (timeout, http 5xx, http
    # 4xx, database, validation, unknown).
    present, summary = classify_run_error(
        "request timed out after HTTP 500 from database"
    )
    assert present is True
    assert summary == "timeout"
