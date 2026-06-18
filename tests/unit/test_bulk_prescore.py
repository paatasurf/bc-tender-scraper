"""Unit tests for bulk construction prescore."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.bulk_prescore import (
    count_pending_construction_companies,
    list_pending_construction_company_ids,
    prescore_construction_company,
    run_bulk_prescore_batch,
)


def test_count_pending_construction_companies():
    session = MagicMock()
    session.scalar.return_value = 42

    assert count_pending_construction_companies(session) == 42
    session.scalar.assert_called_once()


def test_list_pending_construction_company_ids():
    session = MagicMock()
    session.scalars.return_value.all.return_value = [1, 2, 3]

    assert list_pending_construction_company_ids(session, limit=100) == [1, 2, 3]


@patch("pipeline.bulk_prescore._ensure_prescore_marker", return_value=False)
@patch("pipeline.bulk_prescore.warm_hybrid_tender_cache")
@patch("pipeline.bulk_prescore._scan_construction_rule_tenders")
@patch("pipeline.bulk_prescore.CompanySignals.from_company")
def test_prescore_construction_company_scores_top_pairs(
    mock_signals,
    mock_scan,
    mock_warm,
    _mock_marker,
):
    company = MagicMock()
    company.id = 99
    company.name = "Acme Builders"

    session = MagicMock()
    session.get.return_value = company

    candidate = MagicMock()
    candidate.tender_source = "federal"
    candidate.tender_id = 7
    candidate.rule_score = 80
    candidate.reasons = ["region"]
    mock_scan.return_value = [candidate]
    mock_warm.return_value = {"freshly_scored": 1, "cache_hits": 0}

    result = prescore_construction_company(session, 99, pair_limit=5)

    assert result["status"] == "scored"
    assert result["company_id"] == 99
    assert result["candidates_sent"] == 1
    mock_warm.assert_called_once()
    assert mock_warm.call_args.kwargs["inline_cap"] is None


@patch("pipeline.bulk_prescore._ensure_prescore_marker", return_value=True)
@patch("pipeline.bulk_prescore._scan_construction_rule_tenders", return_value=[])
@patch("pipeline.bulk_prescore.CompanySignals.from_company")
def test_prescore_construction_company_inserts_marker_when_no_candidates(
    _mock_signals,
    _mock_scan,
    _mock_marker,
):
    company = MagicMock()
    company.id = 12
    company.name = "Quiet Co"

    session = MagicMock()
    session.get.return_value = company

    result = prescore_construction_company(session, 12)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_rule_candidates"
    assert result["marker_inserted"] is True


@patch("pipeline.bulk_prescore.prescore_construction_company")
@patch("pipeline.bulk_prescore.list_pending_construction_company_ids", return_value=[1, 2])
@patch("pipeline.bulk_prescore.count_pending_construction_companies", side_effect=[5, 3])
def test_run_bulk_prescore_batch_aggregates_stats(mock_count, mock_list, mock_prescore):
    mock_prescore.side_effect = [
        {"status": "scored", "rule_scanned": 10, "candidates_sent": 2, "freshly_scored": 2},
        {"status": "skipped", "rule_scanned": 0, "candidates_sent": 0},
    ]
    session = MagicMock()

    result = run_bulk_prescore_batch(session, batch_size=100)

    assert result["pending_before"] == 5
    assert result["pending_after"] == 3
    assert result["companies_processed"] == 2
    assert result["companies_scored"] == 1
    assert result["companies_skipped"] == 1
    assert result["freshly_scored"] == 2
    assert mock_prescore.call_count == 2
