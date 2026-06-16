"""Verify opportunities discovery uses short-lived sessions per phase."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from pipeline.opportunity_discovery import (
    DiscoveryReadBundle,
    RuleTenderCandidate,
    SessionPhaseMetrics,
    _discover_architecture_opportunities,
    _discover_construction_opportunities,
)


def _minimal_construction_bundle() -> DiscoveryReadBundle:
    company = MagicMock()
    company.id = 1921
    signals = MagicMock()
    return DiscoveryReadBundle(
        company=company,
        signals=signals,
        tender_rows=[],
        permit_rows=[],
        award_rows=[],
        fresh_cache={},
    )


def _minimal_arch_bundle() -> DiscoveryReadBundle:
    company = MagicMock()
    company.id = 19
    signals = MagicMock()
    return DiscoveryReadBundle(
        company=company,
        signals=signals,
        tender_rows=[],
        permit_rows=[],
        award_rows=[],
        fresh_cache={},
        cached_tender_rows={},
    )


@contextmanager
def _tracking_session_scope(closes: list[int]):
    session = MagicMock()
    closes.append(0)

    @contextmanager
    def inner():
        yield session
        session.close()
        closes[0] += 1

    with inner() as s:
        yield s


@pytest.mark.parametrize(
    ("discover_fn", "company_id", "bundle"),
    [
        (_discover_construction_opportunities, 1921, _minimal_construction_bundle()),
        (_discover_architecture_opportunities, 19, _minimal_arch_bundle()),
    ],
)
def test_discover_closes_session_between_phases(discover_fn, company_id, bundle):
    closes: list[int] = []
    scope_calls = {"count": 0}

    @contextmanager
    def mock_scope():
        scope_calls["count"] += 1
        session = MagicMock()
        yield session
        session.close()
        closes.append(scope_calls["count"])

    hybrid_result = {
        "pairs": {},
        "cache_hits": 0,
        "freshly_scored": 0,
    }

    with (
        patch("pipeline.opportunity_discovery.session_scope", side_effect=mock_scope),
        patch(
            "pipeline.opportunity_discovery._load_construction_read_bundle",
            return_value=bundle,
        ) if discover_fn is _discover_construction_opportunities else patch(
            "pipeline.opportunity_discovery._load_architecture_read_bundle",
            return_value=bundle,
        ),
        patch("pipeline.opportunity_discovery._finalize_read_bundle"),
        patch(
            "pipeline.opportunity_discovery._scan_construction_rule_tenders_from_rows",
            return_value=[],
        ) if discover_fn is _discover_construction_opportunities else patch(
            "pipeline.opportunity_discovery._scan_architecture_rule_tenders_from_rows",
            return_value=[],
        ),
        patch(
            "pipeline.opportunity_discovery._run_hybrid_tender_scoring",
            return_value=hybrid_result,
        ),
        patch("pipeline.opportunity_discovery.load_fresh_company_tender_matches", return_value=[]),
        patch(
            "pipeline.opportunity_discovery._rule_tenders_to_opportunity_items",
            return_value=([], []),
        ),
        patch(
            "pipeline.opportunity_discovery._cached_ai_tenders_to_opportunity_items",
            return_value=([], []),
        ),
        patch(
            "pipeline.opportunity_discovery._attach_final_construction_tender_breakdowns",
            return_value=0,
        ),
        patch("pipeline.opportunity_discovery._batch_load_tender_rows", return_value={}),
    ):
        discover_fn(company_id, limit=15, max_candidates=10, metrics=SessionPhaseMetrics())

    # construction: read + hybrid + final breakdown = 3; architecture: read + hybrid = 2
    expected = 3 if discover_fn is _discover_construction_opportunities else 2
    assert scope_calls["count"] == expected
    assert len(closes) == expected


def test_session_phase_metrics_db_total():
    metrics = SessionPhaseMetrics(read_ms=100, hybrid_write_ms=200, final_db_ms=50)
    assert metrics.db_total_ms == 350
