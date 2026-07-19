"""Regression tests for the compute_enrichment_status import fix (PR-E4.0).

Before this fix, pipeline/company_intelligence.py called
compute_enrichment_status(company) in analyze_companies_ai() and
enrich_companies_google() without ever importing it -- every call raised
NameError, silently caught by the surrounding `except Exception`, which
then rolled back the whole transaction (undoing the score/summary/
Google-field assignments made just before it). These tests prove the
fix: both functions now reach session.commit() on the success path, the
status field is actually computed, and compute_enrichment_status is a
plain import (not a copy) from pipeline.company_classification.

Fully mocked -- no real Anthropic/Google API calls, no real sleep, no
database connection.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pipeline.company_classification as company_classification
import pipeline.company_intelligence as ci
from db.models import Company

# ===================================================================
# Fixture builders
# ===================================================================


def _make_company(**overrides) -> Company:
    defaults = dict(
        name="Acme Construction Ltd",
        total_projects=12,
        total_value=5_000_000.0,
        avg_project_value=416_666.0,
        project_types=["Commercial"],
        neighborhoods=["Mount Pleasant"],
        first_project_date="2020-01-01",
        last_project_date="2026-06-01",
        google_rating=None,
        google_reviews_count=None,
        ai_reliability_score=None,
        ai_summary="",
    )
    defaults.update(overrides)
    return Company(**defaults)


# ===================================================================
# 4. Static/import identity check
# ===================================================================


def test_compute_enrichment_status_is_imported_not_duplicated():
    """pipeline.company_intelligence.compute_enrichment_status must be
    the exact same function object as pipeline.company_classification's
    -- an import, never a copy."""
    assert hasattr(ci, "compute_enrichment_status")
    assert (
        ci.compute_enrichment_status is company_classification.compute_enrichment_status
    )


def test_compute_enrichment_status_not_redefined_in_company_intelligence_source():
    source = pathlib.Path(ci.__file__).read_text(encoding="utf-8")
    assert "def compute_enrichment_status" not in source


def test_compute_enrichment_status_still_lives_in_company_classification():
    import inspect

    source_file = inspect.getsourcefile(
        company_classification.compute_enrichment_status
    )
    assert source_file is not None
    assert pathlib.Path(source_file).name == "company_classification.py"


# ===================================================================
# 1. analyze_companies_ai() -- success path now actually commits
# ===================================================================


def test_analyze_companies_ai_calls_status_computation(monkeypatch):
    call_log: list[Company] = []
    real_compute = company_classification.compute_enrichment_status

    def _spy(company):
        call_log.append(company)
        return real_compute(company)

    monkeypatch.setattr(ci, "compute_enrichment_status", _spy)
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ci, "_analyze_company", lambda client, company: (85, "Solid track record.")
    )

    company = _make_company()
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    ci.analyze_companies_ai(session)

    assert call_log == [company]


def test_analyze_companies_ai_saves_score_summary_status_and_commits(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ci, "_analyze_company", lambda client, company: (85, "Solid track record.")
    )

    company = _make_company(google_reviews_count=10)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    result = ci.analyze_companies_ai(session)

    assert result == 1
    assert company.ai_reliability_score == 85
    assert company.ai_summary == "Solid track record."
    assert company.enrichment_status == "complete"  # google + ai both present
    session.commit.assert_called_once()


def test_analyze_companies_ai_no_longer_rolls_back_on_success(monkeypatch):
    """The regression this whole PR exists to fix: prior to the import
    fix, this exact scenario always hit session.rollback() via the
    NameError, never session.commit()."""
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ci, "_analyze_company", lambda client, company: (85, "Solid track record.")
    )

    company = _make_company()
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    result = ci.analyze_companies_ai(session)

    assert result == 1
    session.rollback.assert_not_called()
    session.commit.assert_called_once()


# ===================================================================
# 2. enrich_companies_google() -- success path now actually commits
# ===================================================================


def test_enrich_companies_google_calls_status_computation(monkeypatch):
    call_log: list[Company] = []
    real_compute = company_classification.compute_enrichment_status

    def _spy(company):
        call_log.append(company)
        return real_compute(company)

    monkeypatch.setattr(ci, "compute_enrichment_status", _spy)
    monkeypatch.setattr(ci, "_google_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ci,
        "_fetch_google_place",
        lambda api_key, name: {
            "rating": 4.7,
            "userRatingCount": 88,
            "formattedAddress": "123 Main St, Vancouver, BC",
            "nationalPhoneNumber": "+1 604-555-0100",
        },
    )

    company = _make_company(google_reviews_count=None)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    ci.enrich_companies_google(session)

    assert call_log == [company]


def test_enrich_companies_google_saves_fields_status_and_commits(monkeypatch):
    monkeypatch.setattr(ci, "_google_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ci,
        "_fetch_google_place",
        lambda api_key, name: {
            "rating": 4.7,
            "userRatingCount": 88,
            "formattedAddress": "123 Main St, Vancouver, BC",
            "nationalPhoneNumber": "+1 604-555-0100",
        },
    )

    company = _make_company(google_reviews_count=None)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    result = ci.enrich_companies_google(session)

    assert result == 1
    assert company.google_rating == 4.7
    assert company.google_reviews_count == 88
    assert company.google_address == "123 Main St, Vancouver, BC"
    assert company.google_phone == "+1 604-555-0100"
    assert company.enrichment_status == "partial"  # google only, no ai_summary yet
    session.commit.assert_called_once()


def test_enrich_companies_google_no_longer_rolls_back_on_success(monkeypatch):
    """The regression this whole PR exists to fix: prior to the import
    fix, this exact scenario always hit session.rollback() via the
    NameError, never session.commit()."""
    monkeypatch.setattr(ci, "_google_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ci,
        "_fetch_google_place",
        lambda api_key, name: {
            "rating": 4.2,
            "userRatingCount": 10,
            "formattedAddress": "1 Any St, Vancouver, BC",
            "nationalPhoneNumber": "+1 604-555-0199",
        },
    )

    company = _make_company(google_reviews_count=None)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    result = ci.enrich_companies_google(session)

    assert result == 1
    session.rollback.assert_not_called()
    session.commit.assert_called_once()


# ===================================================================
# 3. No real API / sleep / DB -- explicit guards
# ===================================================================


def test_analyze_companies_ai_never_sleeps_for_real(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci, "_analyze_company", lambda client, company: (50, "Ok."))
    sleep_calls = []
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    company = _make_company()
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    ci.analyze_companies_ai(session)

    assert sleep_calls == [ci.REQUEST_DELAY_SECONDS]


def test_enrich_companies_google_never_sleeps_for_real(monkeypatch):
    monkeypatch.setattr(ci, "_google_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(
        ci,
        "_fetch_google_place",
        lambda api_key, name: {
            "rating": 4.0,
            "userRatingCount": 5,
            "formattedAddress": "x",
            "nationalPhoneNumber": "x",
        },
    )
    sleep_calls = []
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    company = _make_company(google_reviews_count=None)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    ci.enrich_companies_google(session)

    assert sleep_calls == [ci.REQUEST_DELAY_SECONDS]
