"""Unit tests for google_enrichment status constants."""

from __future__ import annotations

from db.models import Company
from pipeline.google_enrichment import constants as ge_constants


def test_status_constants_values():
    assert ge_constants.pending == "pending"
    assert ge_constants.enriched == "enriched"
    assert ge_constants.review == "review"
    assert ge_constants.no_match == "no_match"
    assert ge_constants.error == "error"
    assert ge_constants.stale == "stale"


def test_google_enrichment_statuses_tuple_is_complete():
    assert ge_constants.GOOGLE_ENRICHMENT_STATUSES == (
        ge_constants.pending,
        ge_constants.enriched,
        ge_constants.review,
        ge_constants.no_match,
        ge_constants.error,
        ge_constants.stale,
    )


def test_company_model_default_matches_pending_constant():
    column = Company.__table__.columns["google_enrichment_status"]
    assert column.default.arg == ge_constants.pending
