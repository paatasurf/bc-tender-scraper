"""Unit tests for scraper/contract_awards.py parsing logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from scraper.contract_awards import _iter_award_listings, _parse_award_detail


def _make_listing_html(rows: list[tuple[str, str, str, str]]) -> str:
    """Build a minimal HTML table matching contract awards listing structure."""
    row_html = ""
    for title, category, date_str, href in rows:
        row_html += (
            f'<tr><td><a href="{href}">{title}</a></td>'
            f"<td>{category}</td><td>{date_str}</td></tr>"
        )
    return f"<html><body><table><tr><th>Title</th><th>Cat</th><th>Date</th></tr>{row_html}</table></body></html>"


def _make_detail_html(
    *,
    h1: str = "",
    winner: str = "",
    value_sr: str = "",
    value_field: str = "",
    award_date: str = "",
) -> str:
    """Build minimal award detail HTML."""
    parts = []
    if h1:
        parts.append(f"<h1>{h1}</h1>")
    if winner:
        parts.append(f'<span class="contract-company">{winner}</span>')
    val_inner = ""
    if value_sr:
        val_inner = f'<span class="sr-only">{value_sr}</span>'
    elif value_field:
        val_inner = f'<div class="field--item">{value_field}</div>'
    if val_inner:
        parts.append(
            f'<div class="field--name-field-award-contract-total-value">{val_inner}</div>'
        )
    if award_date:
        parts.append(
            f'<div class="field--name-field-award-contract-award-date">'
            f'<div class="field--item">{award_date}</div></div>'
        )
    return f"<html><body>{''.join(parts)}</body></html>"


class TestIterAwardListings:
    def test_yields_listings_from_table(self):
        html = _make_listing_html(
            [
                ("Road Repair", "Construction", "2024-01-15", "/award-notice/123"),
                ("Bridge Work", "Construction", "2024-02-01", "/award-notice/456"),
            ]
        )
        session = MagicMock()
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        with patch("scraper.contract_awards.polite_get", return_value=response):
            results = list(_iter_award_listings(session, "https://example.com"))

        assert len(results) == 2
        assert results[0]["tender_title"] == "Road Repair"
        assert results[0]["url"] == "https://example.com/award-notice/123"
        assert results[1]["date"] == "2024-02-01"

    def test_stops_when_no_table(self):
        html = "<html><body><p>No results</p></body></html>"
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()

        with patch("scraper.contract_awards.polite_get", return_value=response):
            results = list(_iter_award_listings(MagicMock(), "https://example.com"))

        assert results == []

    def test_deduplicates_urls(self):
        html = _make_listing_html(
            [
                ("Road Repair", "Construction", "2024-01-15", "/award-notice/123"),
                ("Road Repair Dup", "Construction", "2024-01-15", "/award-notice/123"),
            ]
        )
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()

        with patch("scraper.contract_awards.polite_get", return_value=response):
            results = list(_iter_award_listings(MagicMock(), "https://example.com"))

        assert len(results) == 1

    def test_skips_rows_without_award_notice_link(self):
        row_html = (
            '<tr><td><a href="/other/page">Not award</a></td>'
            "<td>Cat</td><td>2024-01-01</td></tr>"
        )
        html = f"<html><body><table><tr><th>T</th></tr>{row_html}</table></body></html>"
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()

        with patch("scraper.contract_awards.polite_get", return_value=response):
            results = list(_iter_award_listings(MagicMock(), "https://example.com"))

        assert results == []


class TestParseAwardDetail:
    def test_extracts_all_fields(self):
        html = _make_detail_html(
            h1="Highway Paving Contract",
            winner="Acme Construction Ltd",
            value_sr="$1,500,000.00",
            award_date="2024-03-15",
        )
        soup = BeautifulSoup(html, "html.parser")
        listing = {
            "tender_title": "Highway Paving",
            "date": "2024-01-01",
            "url": "https://example.com/award/1",
        }

        result = _parse_award_detail(soup, listing)

        assert result["tender_title"] == "Highway Paving Contract"
        assert result["winner_company"] == "Acme Construction Ltd"
        assert result["contract_value"] == "$1,500,000.00"
        assert result["date"] == "2024-03-15"
        assert result["url"] == "https://example.com/award/1"

    def test_falls_back_to_listing_title_when_no_h1(self):
        html = _make_detail_html(winner="Winner Corp")
        soup = BeautifulSoup(html, "html.parser")
        listing = {
            "tender_title": "Fallback Title",
            "date": "2024-05-01",
            "url": "http://x.com/1",
        }

        result = _parse_award_detail(soup, listing)

        assert result["tender_title"] == "Fallback Title"

    def test_extracts_value_from_field_item_fallback(self):
        html = _make_detail_html(value_field="$250,000")
        soup = BeautifulSoup(html, "html.parser")
        listing = {
            "tender_title": "Test",
            "date": "2024-06-01",
            "url": "http://x.com/2",
        }

        result = _parse_award_detail(soup, listing)

        assert result["contract_value"] == "$250,000"

    def test_uses_listing_date_when_no_date_element(self):
        html = _make_detail_html(winner="Builder Inc")
        soup = BeautifulSoup(html, "html.parser")
        listing = {
            "tender_title": "Test",
            "date": "2024-07-01",
            "url": "http://x.com/3",
        }

        result = _parse_award_detail(soup, listing)

        assert result["date"] == "2024-07-01"

    def test_empty_fields_when_no_elements_found(self):
        html = "<html><body><p>Empty page</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        listing = {"tender_title": "X", "date": "2024-01-01", "url": "http://x.com/4"}

        result = _parse_award_detail(soup, listing)

        assert result["winner_company"] == ""
        assert result["contract_value"] == ""
        assert result["tender_title"] == "X"
