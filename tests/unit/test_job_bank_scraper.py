"""Unit tests for scraper/job_bank.py parsing logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from scraper.job_bank import (
    BC_LOCATION_PATTERN,
    _clean_label,
    _is_bc_location,
    _iter_search_pages,
    _parse_article,
)


class TestIsBcLocation:
    def test_matches_bc_in_parentheses(self):
        assert _is_bc_location("Vancouver (BC)") is True

    def test_matches_british_columbia(self):
        assert _is_bc_location("Surrey, British Columbia") is True

    def test_case_insensitive(self):
        assert _is_bc_location("burnaby, british columbia") is True

    def test_no_match(self):
        assert _is_bc_location("Toronto, Ontario") is False

    def test_empty_string(self):
        assert _is_bc_location("") is False


class TestCleanLabel:
    def test_removes_location_label(self):
        result = _clean_label("Location Vancouver (BC)", "Location")
        assert result == "Vancouver (BC)"

    def test_removes_salary_label(self):
        result = _clean_label("Salary $30.00 hourly", "Salary")
        assert result == "$30.00 hourly"

    def test_no_label_present(self):
        result = _clean_label("Just text", "Location")
        assert result == "Just text"

    def test_case_insensitive_removal(self):
        result = _clean_label("LOCATION Vancouver (BC)", "Location")
        assert result == "Vancouver (BC)"


def _make_article_html(
    *,
    title: str = "Construction Worker",
    href: str = "/jobsearch/jobposting/12345",
    location: str = "Vancouver (BC)",
    salary: str = "$30.00 hourly",
    date: str = "2024-01-15",
    company: str = "Builder Inc",
) -> str:
    return (
        f'<article class="action-buttons">'
        f'<a class="resultJobItem" href="{href};extra">'
        f'<span class="noctitle">{title}</span></a>'
        f'<li class="location">Location {location}</li>'
        f'<li class="salary">Salary {salary}</li>'
        f'<li class="date">{date}</li>'
        f'<li class="business">{company}</li>'
        f"</article>"
    )


class TestParseArticle:
    def test_extracts_all_fields(self):
        html = _make_article_html()
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")

        result = _parse_article(article)

        assert result is not None
        assert result["job_title"] == "Construction Worker"
        assert result["company"] == "Builder Inc"
        assert result["location"] == "Vancouver (BC)"
        assert result["salary"] == "$30.00 hourly"
        assert result["date"] == "2024-01-15"
        assert "/jobsearch/jobposting/12345" in result["url"]
        assert ";extra" not in result["url"]

    def test_returns_none_for_non_bc_location(self):
        html = _make_article_html(location="Toronto, Ontario")
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")

        result = _parse_article(article)
        assert result is None

    def test_returns_none_without_link(self):
        html = (
            '<article class="action-buttons">'
            '<span class="noctitle">Title</span>'
            '<li class="location">Location Vancouver (BC)</li>'
            "</article>"
        )
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")

        result = _parse_article(article)
        assert result is None

    def test_returns_none_without_title(self):
        html = (
            '<article class="action-buttons">'
            '<a class="resultJobItem" href="/jobsearch/jobposting/1">link</a>'
            '<li class="location">Location Vancouver (BC)</li>'
            "</article>"
        )
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")

        result = _parse_article(article)
        assert result is None

    def test_returns_none_with_empty_href(self):
        html = (
            '<article class="action-buttons">'
            '<a class="resultJobItem" href="">'
            '<span class="noctitle">Title</span></a>'
            '<li class="location">Location Vancouver (BC)</li>'
            "</article>"
        )
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")

        result = _parse_article(article)
        assert result is None

    def test_handles_missing_optional_elements(self):
        html = (
            '<article class="action-buttons">'
            '<a class="resultJobItem" href="/jobsearch/jobposting/99">'
            '<span class="noctitle">Worker</span></a>'
            '<li class="location">Location Surrey (BC)</li>'
            "</article>"
        )
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")

        result = _parse_article(article)

        assert result is not None
        assert result["job_title"] == "Worker"
        assert result["salary"] == ""
        assert result["date"] == ""
        assert result["company"] == ""


class TestIterSearchPages:
    def test_yields_articles_from_pages(self):
        html_with_articles = (
            "<html><body>"
            '<article class="action-buttons"><p>job1</p></article>'
            '<article class="action-buttons"><p>job2</p></article>'
            "</body></html>"
        )
        html_empty = "<html><body><p>No results</p></body></html>"

        response_with = MagicMock()
        response_with.text = html_with_articles
        response_with.raise_for_status = MagicMock()

        response_empty = MagicMock()
        response_empty.text = html_empty
        response_empty.raise_for_status = MagicMock()

        with patch("scraper.job_bank.polite_get", side_effect=[response_with, response_empty]):
            pages = list(_iter_search_pages(MagicMock()))

        assert len(pages) == 1
        assert len(pages[0]) == 2

    def test_stops_on_empty_page(self):
        html = "<html><body><p>No results</p></body></html>"
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()

        with patch("scraper.job_bank.polite_get", return_value=response):
            pages = list(_iter_search_pages(MagicMock()))

        assert pages == []
